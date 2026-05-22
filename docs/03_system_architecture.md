# 03. 시스템 아키텍처 (System Architecture)

> **상위**: [00](00_overview.md) / [01](01_market_landscape.md) / [02](02_use_cases_and_personas.md)
> **다음**: [04_llm_agent_layer.md](04_llm_agent_layer.md)

이 문서는 GAPT의 **고수준 시스템 구조**를 정의한다. 컨트롤 플레인과 데이터/실행 플레인의 분리, 도메인 경계, 핵심 데이터 모델, 주요 데이터 흐름(요청·이벤트·배포)을 다룬다. 깊은 세부 사항은 04~10 문서로 위임한다.

---

## 3.1 아키텍처의 두 평면 (Two Planes)

GAPT는 **컨트롤 플레인(Control Plane)** 과 **실행 플레인(Execution Plane)** 을 엄격하게 분리한다.

### 컨트롤 플레인 — "토킷 백엔드"
- 호스트 OS에 1개 인스턴스(M0~M3, 단일 노드).
- 사용자 계정, 프로젝트 메타데이터, 권한, 시크릿, 감사 로그, 작업 큐.
- 외부 서비스(GitHub API, Anthropic API, etc.) 호출.
- **호스트 docker 소켓에 직접 접근**할 수 있는 유일한 컴포넌트 (실행 플레인 생성 목적으로만).

### 실행 플레인 — "프로젝트 박스"
- 프로젝트당 1개의 **Sysbox 격리 컨테이너**.
- 그 안에 사용자 코드(`git clone`된 외부 레포), `dockerd`(Sysbox의 안전한 inner Docker), 사용자 compose 스택, Claude Code CLI 프로세스, PTY 세션이 모두 거주.
- **호스트 docker 소켓을 *절대* 마운트하지 않는다.** 호스트 자원에 직접 접근 불가.

이 분리는 단순한 컨테이너 정리가 아니라 **보안의 1번 원칙**이다. 컨트롤 플레인이 컴프로마이즈되더라도 실행 플레인의 데이터/시크릿이 *직접* 노출되지 않게, 또한 실행 플레인의 침해가 컨트롤 플레인을 거치지 않으면 다른 프로젝트로 번지지 않게 만든다.

```
┌─────────────────────────── HOST OS ──────────────────────────────┐
│                                                                    │
│  ┌──────────────────── CONTROL PLANE ─────────────────────────┐   │
│  │  Toolkit Backend (FastAPI)                                  │   │
│  │  Toolkit Frontend (Caddy 정적 서빙)                          │   │
│  │  ─ Project Service / Auth / Audit                           │   │
│  │  ─ Agent Session Manager (geny-executor)                    │   │
│  │  ─ Sandbox Controller ── (호스트 docker 소켓에 한정 접근) ──┐  │   │
│  │  ─ Caddy reverse proxy (자동 HTTPS, subdomain preview)      │ │   │
│  │  ─ PostgreSQL + Redis + SeaweedFS (영속 파일 코어)          │ │   │
│  └─────────────────────────────────────────────────────────────┘ │   │
│                              │ docker API (제한된 호출만)         │   │
│                              ▼                                     │   │
│  ┌──────────────── EXECUTION PLANE (per-project) ───────────────┐  │
│  │                                                                │  │
│  │  Sysbox Container A (Geny)        Sysbox Container B (...)    │  │
│  │  ┌─────────────────────────┐     ┌─────────────────────────┐ │  │
│  │  │ inner dockerd (Sysbox)  │     │ inner dockerd (Sysbox)  │ │  │
│  │  │ git clone /workspace    │     │ git clone /workspace    │ │  │
│  │  │ compose stack (사용자) │     │ compose stack (사용자)  │ │  │
│  │  │ claude (Claude Code CLI)│     │ claude                  │ │  │
│  │  │ PTY 세션들              │     │ PTY 세션들              │ │  │
│  │  └─────────────────────────┘     └─────────────────────────┘ │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
            │                                       │
            ▼                                       ▼
   ┌──────────────────┐                   ┌──────────────────────┐
   │ 외부 Git Host    │                   │ Anthropic API        │
   │ (GitHub/Gitea/   │                   │ (Claude Code SDK)    │
   │  GitLab)         │                   │  + MCP servers       │
   └──────────────────┘                   └──────────────────────┘
```

---

## 3.2 컨트롤 플레인 도메인 분할

컨트롤 플레인은 다음 8개 도메인(=서비스 모듈)으로 분할한다. 모두 같은 FastAPI 프로세스에 거주하지만 *모듈 경계*는 엄격.

| # | 도메인 | 책임 | 외부 의존 |
|---|---|---|---|
| D1 | **Project Service** | 프로젝트 CRUD, 메타데이터, 환경(env) 정의, 워크트리 매핑 | DB |
| D2 | **Auth Service** | 사용자, 세션 토큰, IDP 어댑터, RBAC | DB, IDP |
| D3 | **Git Service** | 원격 Git 인증·clone·fetch·push·PR 생성 | GitHub/GitLab API, `git` CLI |
| D4 | **Sandbox Controller** | Sysbox 컨테이너 라이프사이클, 리소스 한계, 헬스 | 호스트 dockerd (제한된 호출) |
| D5 | **Agent Session Manager** | LLM 세션 라이프사이클, 스트리밍, 비용 추적. `EnvironmentManifest` resolve + `Pipeline.from_manifest_async` 호출 | geny-executor 2.1.0+, Anthropic API (또는 manifest가 지정한 다른 provider) |
| D6 | **Build/Deploy Orchestrator** | inner/outer loop, CI 트리거, 배포 hook | Sandbox Ctrl, 외부 CI API |
| D7 | **Secret Vault** | 시크릿 보관·주입·회수 | OS keyring / SOPS / Infisical |
| D8 | **Audit & Observability** | 구조화 이벤트 수집, 비용 집계, OTel 트레이싱, 대시보드 | DB, OTel exporter |

**서비스 간 의존성 규칙** (단방향):
```
D2 Auth ──┐
          ▼
D1 Project ──→ D3 Git ──┐
          │             │
          ▼             ▼
D5 Agent Session Mgr ──→ D4 Sandbox Ctrl
          │             │
          ▼             ▼
D6 Build/Deploy ────────┘
          │
          ▼
D7 Secret Vault, D8 Audit (모두가 의존, 단 D7/D8은 다른 서비스에 의존 안 함)
```

순환 의존 금지. D7과 D8은 *항상 가장 안쪽 의존성*.

---

## 3.3 핵심 데이터 모델 (요약)

상세 스키마는 후속 문서로 위임. 여기는 *개념 모델*만.

```
User
 ├─ id, email, display_name, created_at
 └─ Membership[*] (Org 가능, M0에선 단일 org "default")
       └─ Role: viewer | editor | admin | owner

Project
 ├─ id, slug, display_name, created_at, owner_id
 ├─ git_remote (url, provider, auth_ref → Vault)
 ├─ default_compose_file (auto-detected: compose.dev.yml etc.)
 ├─ Environment[*]
 │   ├─ name (dev | staging | prod | custom)
 │   ├─ deploy_target (local | remote-ssh | webhook | k8s …)
 │   ├─ secret_refs[*] → Vault
 │   └─ pre/post hooks
 ├─ Workspace[*]              # 활성 워크트리들
 │   ├─ id, branch, worktree_path
 │   ├─ sandbox_container_id  # FK → Sandbox
 │   └─ port_assignments {...}
 └─ Membership[*] (project-level role override)

Sandbox
 ├─ id, project_id, workspace_id
 ├─ status (creating | running | paused | stopped | failed)
 ├─ resource_limits (cpu, mem, pids, net policy)
 ├─ inner_dockerd_state
 └─ last_activity_at

AgentSession
 ├─ id, project_id, workspace_id, user_id
 ├─ pipeline_preset (gapt_default | gapt_planning | …)
 ├─ status (active | stale_idle | stale_compact | archived)
 ├─ messages[*]  (대화 기록)
 ├─ tool_invocations[*] (감사용)
 ├─ cost { input_tok, output_tok, usd }
 └─ created_at, last_active

Secret
 ├─ id, owner_id, scope (user | project | environment)
 ├─ backend (keyring | sops | infisical)
 ├─ key_id (백엔드의 식별자, 평문 값은 DB에 저장 안 함)
 └─ injected_into[*] (어떤 컨테이너/세션에 단명 주입됐는지 감사)

AuditEvent
 ├─ id (ULID, 시간 정렬)
 ├─ actor (user_id | agent_session_id | system)
 ├─ scope (project_id, workspace_id, env_id)
 ├─ action (literal enum: project.create, agent.tool_invoke, deploy.exec, secret.read, …)
 ├─ payload (JSON, 도구별 구조화 데이터)
 └─ outcome (ok | error | denied)
```

키 결정:
- **ULID** (Universally Unique Lexicographically Sortable ID)를 1차 PK로. UUID v7도 동등 옵션. Postgres의 `uuid` 또는 `text` 컬럼 (ULID는 26자 lexicographic) 둘 다 가능.
- 모든 시크릿 값은 *백엔드에 위임*. DB엔 참조만.
- AgentSession ↔ Workspace는 다대일 (한 워크트리에 여러 세션 동시 가능).
- **DB는 M0부터 PostgreSQL** — SQLite 시작 후 마이그레이션 경로 같은 것 없음. 운영 단순성보다 *데이터 모델 일관성*과 *동시 쓰기*가 우선.

→ 자세한 ERD/마이그레이션은 04, 09에서 다룸.

---

## 3.4 컨트롤 ↔ 실행 플레인 통신 프로토콜

**원칙**: 컨트롤 플레인은 실행 플레인 컨테이너의 *내부*에 SSH/exec로 들어가지 않는다. 대신:

1. **Sandbox 생성/관리**: `docker create / start / stop / inspect / events` — Sandbox Controller만 사용.
2. **컨테이너 안 작업 실행**: 컨트롤 플레인은 *컨테이너 안의 toolkit-agent 데몬*에게 RPC.
   - 컨테이너 이미지에 미리 들어 있는 작은 데몬 (`toolkit-agent`, Python ~5MB).
   - 컨트롤 플레인 ↔ 데몬: Unix socket(컨테이너 마운트) 또는 컨테이너 내부 HTTP + 컨트롤 측에서 docker network 접근.
   - 인증: 단명 토큰(JWT, 5분), 컨테이너 생성 시 주입.
3. **PTY 세션**: 데몬이 PTY를 자기 안에서 만들고, 컨트롤 플레인은 WebSocket으로 데몬에 stdin/stdout 릴레이. xterm.js ↔ FastAPI WS ↔ 데몬 PTY.

```
[브라우저 xterm] ─WS─→ [Toolkit Backend] ─unix/http─→ [toolkit-agent 데몬] ─pty→ [컨테이너 내 bash]
```

데몬을 두는 이유:
- 컨트롤 플레인이 `docker exec`를 매번 호출하면 *호스트 dockerd*에 의존이 깊어진다. 데몬을 매개로 하면 향후 K8s/원격 호스트 전환 시 *데몬 RPC만 동일하게 노출*하면 된다.
- 데몬이 컨테이너 안의 *권한 경계*를 강제할 수 있다 (예: 특정 디렉토리 외 접근 거부).
- 감사 이벤트의 *컨테이너 측 발생점*이 명확해진다.

---

## 3.5 주요 데이터 흐름 3종

### F1. 사용자 메시지 → LLM 응답 스트림

```
사용자 입력 → POST /api/sessions/{sid}/invoke (FastAPI)
   → Agent Session Manager
       → (Workspace 컨텍스트 조회)
       → (Project Secret 단명 조회 → Vault)
       → geny-executor Pipeline.run_stream(state)
           → Stage 1~21 진행
               (Stage 6 API: Anthropic API 호출)
               (Stage 10 Tool: 도구 실행 — 컨테이너 안 데몬으로 위임 가능)
           → AsyncIterator[PipelineEvent]
       → Audit (각 tool_invoke마다 1 event)
   → SSE 스트림으로 브라우저에 토큰 단위 푸시
```

브라우저 측은 SSE를 수신하면서 Monaco diff 뷰어 / 채팅 패널을 동시 업데이트. 사용자의 인터럽트는 별도 `POST /api/sessions/{sid}/interrupt`로.

### F2. Git push → CI 결과 표시

```
[Inner loop]
  사용자가 채팅에서 "커밋해줘" → 에이전트 도구 호출
   → toolkit-agent 데몬 → `git add/commit/push` (컨테이너 내부)
   → Git Service가 GitHub 웹훅 또는 polling으로 CI 시작 감지
   → Audit / Project Service에 기록

[Outer loop]
  GitHub Actions가 작업 시작 → workflow_run webhook
   → Toolkit Backend가 webhook 수신 (HMAC 검증)
   → Build/Deploy Orchestrator가 진행 상황 polling (gh API)
   → SSE로 사용자 워크스페이스 UI에 스트림
```

### F3. 배포 (사용자 트리거)

```
사용자가 UI에서 "Deploy to prod" 클릭 → POST /api/environments/{env_id}/deploy
   → Build/Deploy Orchestrator
       → 2FA 확인 (prod인 경우)
       → Secret Vault에서 .env.prod 평문 단명 조회
       → deploy_target에 따라:
           - local        → 자기 호스트 sandbox에 시크릿 주입 후 compose up
           - remote-ssh   → SSH로 원격 호스트에 compose 실행
           - webhook      → 사용자 정의 webhook 호출 (HMAC 서명)
           - k8s (M4+)    → kubectl apply / argo sync
       → 진행 로그 SSE 스트림
   → Audit (deploy.start / deploy.complete / deploy.fail)
```

배포가 *컨트롤 플레인의 1급 액션*인 것이 본 toolkit의 차별점. Cursor/Continue가 못 하는 영역.

---

## 3.6 모듈 경계와 인터페이스

각 도메인 D1~D8은 다음 형태의 인터페이스 뒤에 캡슐화된다(추후 D3/D4/D7을 *플러그인 어댑터*화하기 위한 준비). **D5(Agent Session)는 어댑터 인터페이스를 두지 않는다** — geny-executor의 `Pipeline.from_manifest_async`가 이미 추상화의 정점이므로 그 위에 한 겹 더 두면 이중 추상화. ProjectAwareSessionManager가 직접 호출. ([04](04_llm_agent_layer.md) §4.11)

```python
# 의사 코드 — 실제 구현은 04 이후
class GitProvider(Protocol):
    async def clone(self, remote, target_path, auth) -> RepoRef: ...
    async def open_pr(self, repo, branch, title, body, auth) -> PRRef: ...
    # 구현: GithubProvider, GiteaProvider, GitlabProvider, …

# LLM 에이전트 추상화 — *별도 어댑터 인터페이스를 두지 않는다.*
# GAPT의 ProjectAwareSessionManager가 직접 geny-executor 2.1.0의
# Pipeline.from_manifest_async(...)를 호출. 테스트는 manifest에 MockProvider
# 지정으로 충분. 두 번의 추상화 레이어 회피. ([04](04_llm_agent_layer.md) §4.11)

class SandboxBackend(Protocol):
    async def create(self, project_id, image, resources, secrets) -> SandboxRef: ...
    async def exec(self, sandbox_ref, cmd) -> ExecResult: ...
    async def open_pty(self, sandbox_ref, shell) -> PtyRef: ...
    # 구현: SysboxBackend (1차), DockerRuncBackend (테스트), KubernetesBackend (M4+)

class SecretBackend(Protocol):
    async def store(self, scope, key, value) -> SecretRef: ...
    async def read(self, secret_ref) -> str: ...
    async def inject(self, sandbox_ref, secret_refs, ttl) -> None: ...
    # 구현: OsKeyringBackend, SopsBackend, InfisicalBackend, VaultBackend

class AuthIdp(Protocol):
    async def authenticate(self, request) -> Optional[User]: ...
    async def callback(self, oauth_state) -> User: ...
    # 구현: MagicLinkIdp, AuthentikIdp (OIDC), KeycloakIdp, …
```

**위 인터페이스는 4개로 정정**: Git / Sandbox / Secret / Auth IDP. **LLM 에이전트는 인터페이스 추상화를 두지 않는다** — geny-executor의 `Pipeline.from_manifest_async`가 *이미* 추상화의 정점이므로 그 위에 어댑터 레이어를 두면 두 번 추상화하는 셈이 된다. ProjectAwareSessionManager가 직접 호출. ([[reference_geny_executor_v2_1]])

다른 모듈은 위 4개 인터페이스의 *직접 구현 클래스 import 금지*, Protocol 타입만 의존.

---

## 3.7 의존성 / 부팅 순서

부팅 시 컨트롤 플레인은 다음 순서로 초기화한다:

```
1. Settings 로드 (env, settings.json, secrets)
2. PostgreSQL/Redis 연결 + 마이그레이션
3. **SeaweedFS Master/Filer 헬스체크** (compose로 함께 부팅된 영속 파일 코어)
4. Vault 어댑터 인스턴스화 (D7)
5. Audit 어댑터 인스턴스화 (D8) — 이후 모든 모듈이 audit 사용 가능
6. Auth IDP 어댑터 인스턴스화 (D2)
7. Sandbox 어댑터 인스턴스화 (D4) — 호스트 dockerd 연결 확인 + SeaweedFS 볼륨 드라이버 등록
8. Git 어댑터 (D3), Project Service (D1)
9. Agent Session Manager (D5) — geny-executor 라이브러리 import
10. Build/Deploy Orchestrator (D6)
11. FastAPI 라우터 마운트, Caddy 핫리로드
12. (백그라운드) ARQ 워커 시작, TickEngine 시작
13. (백그라운드) 좀비 sandbox 정리 워크
```

각 단계 실패는 *명시적 에러*로 종료. "조용히 계속"하지 않는다.

---

## 3.8 멀티 노드 / K8s 백엔드 (M4+) 사전 고려

현재(M0~M3)는 단일 노드 Docker Compose 모드. M4부터 K8s/멀티 노드를 지원하려면 다음이 필요:

1. **SandboxBackend의 K8s 구현**: Pod = 컨테이너, namespace 격리, NetworkPolicy.
2. **상태의 외부화**: PostgreSQL은 외부 매니지드/HA로 이동, 영속 파일은 *이미 SeaweedFS 사용 중*이므로 SeaweedFS 클러스터를 *멀티 노드*로 확장만 (Filer/Volume Server 분리).
3. **데몬 RPC의 네트워크 친화화**: Unix socket → mTLS HTTP.
4. **Caddy → Traefik (또는 Caddy K8s ingress)**: 동적 subdomain.
5. **컨트롤 플레인 자체의 HA**: 단일 process → 다중 replica + leader election (Redis 또는 etcd).

**이번 라운드의 작업**: K8s 백엔드를 *지금 구현하지 않는다*. 단 인터페이스(`SandboxBackend`)가 K8s 구현을 *허락*하는 모양이 되도록 처음부터 짠다. 이게 P3(사내 플랫폼)가 M4 이후 막히지 않게 하는 핵심.

---

## 3.9 단일 사용자 ↔ 멀티 사용자 토글

M0~M2: *단일 사용자 모드*로 부팅 (basic auth/magic-link). UI에 다른 사용자 개념 없음.

M3+: *멀티 사용자 모드* 활성화 가능. 토글 시:
- Auth IDP를 magic-link → OIDC(Authentik) 등으로 교체.
- 모든 DB 행에 이미 owner_id가 있으므로 마이그레이션 불필요.
- UI에 Org/Member 패널 노출.

처음부터 모든 데이터 모델에 owner_id를 두는 이유.

---

## 3.10 컨트롤 플레인의 단일 실패점 (SPOF)

| 컴포넌트 | SPOF 여부 | 완화 |
|---|---|---|
| FastAPI 프로세스 | 예 (단일 프로세스) | systemd 자동 재시작 + 헬스체크 |
| PostgreSQL | 예 (M0~M3 단일 인스턴스) | pg_dump 정기 + WAL-G 옵션, M4+ replication |
| Redis | 예 | 부팅 시 AOF로 복구 |
| SeaweedFS Master/Filer | 예 (단일 노드 M0~M3) | replication factor 설정 (`-defaultReplication=001` 등), M4+ 멀티 Volume Server |
| 호스트 dockerd | 예 | 호스트 OS 수준 모니터링 |
| 외부 Anthropic API | 예 | 캐시(Stage 5), 모델 폴백 (Aider 어댑터) |
| 외부 GitHub | 예 | Forgejo 옵션 (M3+) |

단일 노드/사용자 단계에선 *완벽한 가용성*보다 *명확한 실패 표시 + 빠른 재시작*에 집중. P3가 가까워지면 (M4+) HA 설계로 진화.

---

## 3.11 본 문서의 인터페이스가 보장하는 것

설계의 *불변식*으로 두고 후속 문서가 어겨선 안 됨:

1. 컨트롤 플레인이 실행 플레인 안에 SSH/exec 직접 진입하지 않는다.
2. 호스트 docker 소켓은 *Sandbox Controller만* 접근한다.
3. 시크릿 평문은 *DB에 저장되지 않는다*.
4. 모든 audit 이벤트는 구조화 JSON이며 ULID 시간 정렬된다.
5. 5개 플러그인 인터페이스(Git/LLM/Sandbox/Secret/Auth) 외 직접 구현 의존 금지.
6. 어떤 컴포넌트도 owner_id 없이 행을 만들지 않는다.
7. 부팅 실패 시 *조용히 계속*하지 않는다 — 명시적 종료.

이 불변식들을 가지고 다음 문서 04부터는 *내부 구현*으로 들어간다.
