# 09. 보안 / 권한 / 감사 / 관측 (Security · AuthZ · Audit · Observability)

> **상위**: [03](03_system_architecture.md) / [06](06_isolation_and_runtime.md)
> **다음**: [10_tech_stack_decisions.md](10_tech_stack_decisions.md)

이 문서는 GAPT의 **신뢰 모델**을 정의한다. 인증(Auth) / 권한(RBAC) / 시크릿 관리 / 감사(Audit) / 관측(Observability) 다섯 축. P3(사내 플랫폼 엔지니어)가 *M4+에 도달했을 때* 이 토대에서 빠지는 게 없도록 *처음부터* 설계한다.

핵심 결정 7개:

1. **인증 = IDP-pluggable.** M0~M2 magic-link, M3+ OIDC(Authentik), M4+ SAML 옵션.
2. **권한 = User → Org → Project → Environment 4계층.** 단일 사용자 모드에서도 owner_id 모든 행에.
3. **에이전트 권한 ⊆ 사용자 권한** — LLM이 사용자보다 더 못 한다.
4. **시크릿 = 어댑터 (OS keyring / SOPS / Infisical / Vault).** 평문은 DB 절대 X.
5. **감사 = 구조화 ULID 이벤트.** 모든 도구 호출/배포/시크릿 read.
6. **관측 = OpenTelemetry GenAI semantic conventions 채택**, Prometheus/Loki/Tempo 표준.
7. **데이터는 사용자 호스트 한정.** 외부 텔레메트리 라우터는 *기본 OFF + opt-in*.

---

## 9.1 인증 (Auth)

### 9.1.1 IDP 어댑터 인터페이스

```python
class AuthIdp(Protocol):
    async def begin_login(self, request) -> LoginInitiation: ...
    async def callback(self, params) -> Optional[User]: ...
    async def end_session(self, user_id) -> None: ...
    capability: set[Literal["sso", "mfa", "scim", "magic_link", "device_flow"]]
```

### 9.1.2 구현체

| IDP | M0 | M3+ | 비고 |
|---|---|---|---|
| **MagicLinkIdp** | ★ | 보조 | 이메일 → 1회용 토큰 링크. 단순 |
| **BasicAuthIdp** | (개발) | — | 셋업 OFF |
| **AuthentikIdp** (OIDC) | — | ★ | 셀프호스트 IDP, 모던 UI |
| **KeycloakIdp** | — | ✅ | 엔터프라이즈 표준 (JVM 무거움) |
| **ZitadelIdp** | — | ✅ | 멀티테넌트 1급, API-first |
| **AutheliaIdp** | — | ✅ | reverse proxy 가드 |
| **GenericOidcIdp** | — | ★ | 임의 OIDC 호환 |

**M0~M2**: MagicLink. 단일 사용자 가정 + 이메일 등록.

**M3+**: Authentik 권장 — 셀프호스트 OIDC + SAML, UI 친화. 마이그레이션은 같은 OIDC 표준 안에서 클라이언트 코드 변경 없음.

### 9.1.3 세션 / 토큰

- 웹 세션: HTTP-only secure cookie (서버 측 세션 ID, Redis 저장).
- API 토큰: project-scoped, 만료 가능, 회수 가능. **JWT 아님** (회수 가능성을 위해 DB 참조).
- 데몬 토큰: 단명 (5분), 워크스페이스 단위, mTLS unix socket 부 추가 인증.
- LLM provider 토큰 (Anthropic 등): Secret Vault에서 *매 호출 직전 단명 조회*.

### 9.1.4 MFA

- TOTP (RFC 6238): 모든 사용자 옵션.
- WebAuthn / passkey (M3+): 강력 권장.
- prod 배포 등 위험 액션은 *step-up MFA* (직전 5분 내 인증 재확인).

---

## 9.2 권한 모델 (RBAC)

### 9.2.1 계층

```
User
 └─ MembershipInOrg (Role: viewer | editor | admin | owner)
       └─ Project (소속 Org)
              ├─ ProjectMembership (override Role for this project)
              └─ Environment (dev | staging | prod | custom)
                     ├─ require_2fa: bool
                     └─ access_role: 어떤 role 이상이 deploy/read/edit
```

M0~M2: 단일 사용자 + 단일 Org "default". 모든 권한 owner. 그러나 데이터 모델은 *처음부터* 위 구조 — M3+ retrofit 회피.

### 9.2.2 Role 권한 매트릭스

| 액션 | viewer | editor | admin | owner |
|---|:-:|:-:|:-:|:-:|
| Project 보기 | ✅ | ✅ | ✅ | ✅ |
| Workspace 생성/삭제 | ❌ | ✅ | ✅ | ✅ |
| 채팅/세션 생성 | ✅ | ✅ | ✅ | ✅ |
| 파일 편집 | ❌ | ✅ | ✅ | ✅ |
| git push | ❌ | ✅ | ✅ | ✅ |
| PR 생성 | ❌ | ✅ | ✅ | ✅ |
| Deploy dev/staging | ❌ | ✅ | ✅ | ✅ |
| Deploy prod | ❌ | ❌ | ✅ | ✅ |
| Secret CRUD | ❌ | (자기 것) | ✅ | ✅ |
| 멤버 초대 | ❌ | ❌ | ✅ | ✅ |
| 소유권 이전 | ❌ | ❌ | ❌ | ✅ |
| Project 삭제 | ❌ | ❌ | ❌ | ✅ |

세부는 [10](10_tech_stack_decisions.md)/[11](11_roadmap.md)에서 변경 가능. M3에서 사용자 정의 role 옵션.

### 9.2.3 에이전트 권한 — 기본 deny + config 편집 가능 (PolicyEngine)

핵심 원칙 ([[feedback_policy_config_not_hardcode]]): 위험한 액션을 *코드에 박힌 절대 금지*로 두지 않는다. 대신 **기본값이 strict한 PolicyEngine** 을 두고, 사용자가 config로 자기 책임 하에 완화 가능하게 한다.

**기본 정책 (default policy bundle)** — 사용자가 변경 안 하면 적용:

| 액션 | 기본값 | 비고 |
|---|---|---|
| Deploy to **prod** (LLM 직접) | deny | 사용자 명시적 클릭 + 2FA 필요 |
| Deploy to dev/staging (LLM 직접) | require_user_approval | Plan 카드의 Approve 버튼 |
| Secret CRUD (생성/수정/삭제) | deny | UI 또는 사용자 호출만 |
| Secret read (LLM 도구) | allow (단명, audit) | 시크릿 평문은 컨테이너 환경변수로 단명 |
| 멤버 초대 / 권한 변경 | deny | 사용자 직접만 |
| `Bash(rm -rf ...)` | require_user_approval | 위험 패턴 정규식 매칭 |
| `git push origin main/master` | require_user_approval | protected 브랜치 |
| `git push --force` | deny | (force-with-lease만 require_user_approval) |
| `Edit(.gitignore / .env*)` | require_user_approval | 시크릿 누출 위험 |
| 그 외 화이트리스트 도구 | allow | 카테고리별 |

**PolicyEngine 인터페이스 + 구현 위치**:

PolicyEngine은 *geny-executor의 `HookRunner` 위*에 구현 ([04](04_llm_agent_layer.md) §4.6). `PRE_TOOL_USE` 훅에서 evaluate → deny 시 `ToolFailure(code=ACCESS_DENIED)` 던져 dispatch veto.

```python
class PolicyEngine:
    async def evaluate(
        self,
        action: str,                       # "deploy.prod", "secret.delete", "tool.bash" ...
        actor: Actor,                      # agent_session | user
        scope: Scope,                      # project, env, workspace
        context: dict,                     # 액션별 컨텍스트 (어떤 파일, 어떤 cmd 등)
    ) -> PolicyDecision:                   # allow | deny | require_user_approval | require_2fa
        ...
```

**`claude_code_cli` provider의 2단계 게이트** (CLI 내부 도구는 Stage 10을 거치지 않으므로):
1. CLI의 `settings_path` permission allow-list — 1차 거부.
2. GAPT MCP bridge 안에서 PolicyEngine 재평가 — `mcp__gapt__*` 호출에 audit/policy 유효.

**Policy config 위치** — 4계층 (병합 시 위→아래):

```
1. Built-in default bundle (코드에 내장, 변경 X — *기본값 보장*)
2. Server-wide overrides (관리자 설정, /etc/gapt/policies.yaml)
3. Org overrides (Org 설정 UI)
4. Project overrides (Project 설정 UI, .gapt/policy.yaml 파일도 지원)
```

각 계층은 *덜 제한적인 방향*으로만 override 가능한 게 *기본*. 단 owner 권한 사용자는 *더 제한적*으로 또는 *제한 완화*로 모두 가능 — 단, **완화는 항상 추가 확인 (모달 + audit)**.

**예시 — 사용자가 prod 자동 배포를 활성화하고 싶다**:

```yaml
# project_geny/.gapt/policy.yaml
policies:
  deploy.prod:
    decision: require_user_approval   # default(deny) → require_user_approval로 완화
    additional_guards:
      - ci_must_be_green
      - business_hours_only           # 09~18 KST만
      - cost_cap_session_usd: 10.0
    reason: "내 사이드 프로젝트라 LLM이 야간에 PR 머지 후 자동 배포 시도 OK"
```

이 변경은:
- 저장 시 audit 이벤트 `policy.change` (전/후 diff)
- 사용자 확인 모달: "이 변경은 LLM이 prod에 영향을 줄 수 있게 합니다. 정말 진행?"
- owner-only 액션
- (옵션) Slack/Discord 알림

### 9.2.4 어떤 정책도 *완화*할 수 없는 *불변식*

PolicyEngine의 유연성에도 불구하고 *시스템 자체의 안전*을 위해 *코드 강제* 항목은 존재. 사용자조차 끌 수 없음:

- **호스트 docker 소켓 마운트** — [06](06_isolation_and_runtime.md) §6.3.2.
- **owner_id 없는 행 생성** — [03](03_system_architecture.md) §3.3.
- **시크릿 평문을 DB에 저장** — §9.3.
- **audit 이벤트의 출처 위조** — §9.4.
- **에이전트 세션이 *시스템* PolicyEngine 자체를 호출** — 정책 변경은 사용자/관리자만.

이 5가지 외 모든 정책은 *config 가능*. 5가지는 *변경하려면 코드 fork*.

### 9.2.4 사용자 정의 role / ABAC (M4+)

엔터프라이즈가 들어오면 Role만으로 부족 — 속성 기반 액세스 컨트롤(ABAC)이 필요할 수 있다 (예: "이 GitHub 팀 멤버만 prod"). M4+ Casbin/Cedar 같은 정책 엔진 옵션.

지금은 *role-only* + 인터페이스 추상화로 추후 ABAC 교체 가능하게.

---

## 9.3 시크릿 관리 (Secret Vault)

### 9.3.1 위협

- 평문이 DB에 떨어짐
- 평문이 호스트 디스크 (백업 파일 등)에 떨어짐
- 평문이 LLM 응답에 노출
- 평문이 컨테이너 환경변수로 노출 (`cat /proc/*/environ`)
- 평문이 audit 로그에 떨어짐
- 평문이 git에 commit됨

각각에 대해 *명시적 방어*.

### 9.3.2 SecretBackend 어댑터

```python
class SecretBackend(Protocol):
    async def store(self, scope: SecretScope, key: str, value: str) -> SecretRef: ...
    async def read(self, ref: SecretRef, *, audit_ctx) -> str: ...   # 평문 반환, audit 강제
    async def delete(self, ref: SecretRef) -> None: ...
    async def rotate(self, ref: SecretRef, new_value: str) -> SecretRef: ...
```

| Backend | M0 | M3+ | 비고 |
|---|---|---|---|
| **OsKeyringBackend** (libsecret/SQLite-cipher) | ★ | 보조 | 단일 사용자, 호스트 OS 결합 |
| **SopsAgeBackend** | ✅ | ✅ | git-committable 암호화, age 키 |
| **InfisicalBackend** | — | ★ | 셀프호스트 시크릿 서버, 멀티 사용자 |
| **VaultBackend** (HashiCorp) | — | ✅ | 엔터프라이즈 |
| **OnePasswordBackend** / **DopplerBackend** | — | (옵션) | SaaS — opt-in |

### 9.3.3 단명 주입 패턴

세션/배포 시작 시:
1. SecretBackend.read (평문 1회 가져옴, audit 이벤트)
2. 데몬에게 `inject_secret(env=..., ttl=...)` RPC.
3. 데몬은 tmpfs 파일 + 환경변수에 설정.
4. ttl 만료 또는 세션 종료 시 *명시적 폐기* (tmpfs 파일 삭제, 환경변수 unset).
5. 컨트롤 플레인 메모리에서도 즉시 zeroize.

### 9.3.4 LLM 응답 마스킹

- 알려진 시크릿 값을 LLM 응답에서 정규식 매칭 → `[REDACTED:secret_id]` 치환.
- API 키 패턴 (sk-..., ghp_..., glpat-...)은 *값을 모르더라도* 형태로 탐지 → 사용자 확인.
- 마스킹된 이벤트는 audit에 별도 표시 (`outcome=masked`).

### 9.3.5 .gitignore 강제

- 워크스페이스 부팅 시 `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`가 `.gitignore`에 있는지 자동 검사.
- 없으면 사용자에게 *추가 권유*. LLM이 commit 도구 호출 시 정규식으로 한 번 더 차단.

### 9.3.6 시크릿 회전

- 사용자가 키를 회전하면 새 SecretRef 발급, 활성 세션에 *다음 도구 호출 시* 새 값 주입.
- 회전 이력 audit (회전 전/후 시각, actor).

---

## 9.4 감사 (Audit)

### 9.4.1 이벤트 형식

```json
{
  "id": "01HZ4XPP2K8MEXAMPLE",        // ULID, 시간 정렬
  "ts": "2026-05-22T09:15:23.421Z",
  "actor": {
    "type": "user" | "agent_session" | "system",
    "id": "...",
    "session_id": "...",                // 사용자 웹 세션 또는 에이전트 세션
    "ip": "10.x.x.x",                   // 옵션
    "user_agent": "..."
  },
  "scope": {
    "org_id": "...",
    "project_id": "...",
    "workspace_id": "...",
    "env_id": "..."                     // 선택
  },
  "action": "agent.tool_invoke",        // dotted enum
  "subject": {                          // 무엇에 행위했는가
    "type": "file" | "secret" | "container" | "branch" | ...,
    "id": "...",
    "summary": "src/foo.py +12 −3"
  },
  "outcome": "ok" | "error" | "denied" | "masked",
  "duration_ms": 142,
  "metadata": { /* action-specific */ }
}
```

### 9.4.2 액션 enum (일부)

- `auth.login.success`, `auth.login.fail`, `auth.logout`
- `project.create / update / delete / archive`
- `workspace.create / paused / resumed / deleted`
- `git.clone / fetch / push / pr_create / pr_merge`
- `agent.session.create / interrupt / archive`
- `agent.tool_invoke`, `agent.tool_denied`
- `agent.token.spend` (정기 — 1턴마다)
- `secret.create / read / rotate / delete`
- `deploy.trigger / success / fail / rollback`
- `permission.change / role.assign`
- `system.boot / shutdown / panic`

### 9.4.3 저장 / 보존

- *Hot* (M0~): PostgreSQL append-only 테이블, 인덱스 (ts, scope.project_id, action), 시계열 친화 파티션 (월 단위).
- *Warm* (M3+): Loki, 90일.
- *Cold* (M3+): 압축 JSONL 파일, **SeaweedFS** (S3-호환, Apache-2.0) 또는 사용자 외부 S3, 무기한.
- *Export*: SIEM 친화 NDJSON 파이프 (Vector 어댑터).

### 9.4.4 위변조 방지 (M3+)

- 매 이벤트의 `prev_hash` 필드로 해시 체인.
- 매일 0시 *체크포인트* (서명 가능).
- 손상 감지 시 알림.

### 9.4.5 사용자 액세스

- UI Audit 탭: 본 세션/프로젝트의 이벤트 필터링.
- API: `GET /api/audit?scope=...&action=...&from=...&to=...`
- Export: 사용자가 자기 데이터 일괄 다운로드 (CSV/JSONL).

---

## 9.5 관측 (Observability)

### 9.5.1 메트릭 (Prometheus)

| 카테고리 | 예시 메트릭 |
|---|---|
| 시스템 | `gapt_uptime_seconds`, `gapt_db_connections` |
| 컨테이너 | `gapt_sandbox_count{status}`, `gapt_sandbox_memory_bytes{project,workspace}` |
| 에이전트 세션 | `gapt_agent_sessions{status}`, `gapt_agent_messages_total{project}` |
| LLM | `gen_ai.usage.input_tokens{model,project}`, `gen_ai.cost_usd_total{model,project}` |
| Git | `gapt_git_push_total{provider,outcome}` |
| Deploy | `gapt_deploy_total{env,target,outcome}`, `gapt_deploy_duration_seconds` |

호스트는 `node_exporter` + `cAdvisor`로 컨테이너 리소스 표준 메트릭.

### 9.5.2 트레이싱 (OpenTelemetry)

- 모든 HTTP/SSE/WS 요청 → trace.
- LLM 호출 → child span (`gen_ai.system="anthropic"`, `gen_ai.request.model`, `gen_ai.usage.*`).
- 도구 호출 → child span (`tool.name`, `tool.duration_ms`).
- 데몬 RPC → cross-process trace (HTTP/unix socket header 전파).
- exporter: OTLP → Tempo / Jaeger / Datadog / Honeycomb 사용자 선택.

**OpenTelemetry GenAI semantic conventions** (2025 stable) 채택. 추후 다른 백엔드 도구가 즉시 동작.

### 9.5.3 로그 (Loki / 자체)

- 컨트롤 플레인: 구조화 JSON (loguru/structlog).
- 컨테이너: 데몬이 컨테이너 stdout 캡처 → 컨트롤 플레인 → Loki/파일.
- 키워드 검색 + 시간 범위 + scope 필터.

### 9.5.4 LLM 전용 관측 (Langfuse 옵션)

- M3+ Langfuse 셀프호스트 옵션. LLM 전용 트레이스 / 평가 / dataset.
- 라이선스: MIT + 추가 조항(상용 클라우드 제한). 셀프호스트 무료.

### 9.5.5 대시보드 (Grafana)

- *시스템*: 호스트/컨테이너 리소스, 활성 세션, 에러율.
- *LLM*: 모델별/프로젝트별 토큰·비용 시계열, 평균 응답 시간.
- *Deploy*: 일별 성공률, 평균 소요 시간, 환경별 빈도.
- *Audit summary*: 액션별 카운트, 비정상 패턴 알림.

`grafana` 데이터소스 = Prometheus + Loki + Tempo (모두 셀프호스트). 사용자가 외부 SaaS (Datadog 등)로 라우팅하고 싶으면 OTLP 엔드포인트만 변경.

---

## 9.6 비용 (Cost) — 첫 번째 메트릭

LLM 비용이 토킷의 *가장 끈질긴 관심사*. 별도 강조:

| 차원 | 표시 |
|---|---|
| 세션 누적 USD | 채팅 헤더 라이브 |
| 워크스페이스 일별 | 워크스페이스 페이지 그래프 |
| 프로젝트 누적/일별/월별 | 프로젝트 페이지 |
| 모델별 분포 | Grafana 패널 |
| 사용자별 (멀티 사용자) | 관리자 패널 |
| 도구별 (어떤 도구가 토큰을 많이 쓰는가) | Audit 분석 |

**예산 게이트**:
- 프로젝트별 cost cap (일/월).
- cap 80% 도달 시 사용자 알림.
- 100% 도달 시 새 세션 거부 (override 가능, audit).

---

## 9.7 데이터 보호

- *In transit*: TLS 1.3 모든 외부 연결, mTLS 데몬 ↔ 컨트롤.
- *At rest*: PostgreSQL data dir는 호스트 디스크에 둠 (FUSE 위 RDB는 무결성 위험) + 디스크 암호화 권장. **그 외 모든 영속 파일은 SeaweedFS** (worktree·첨부·audit cold·DB 백업 등). 시크릿은 백엔드별 (keyring/SOPS는 자체 암호화).
- *Backups*: M0~M2 `pg_dump` cron → **동봉 SeaweedFS bucket** 업로드 (외부 S3는 사용자 명시 옵션). M3+ WAL-G continuous archiving도 SeaweedFS 대상.

### 데이터 거주성

사용자 데이터는 *사용자 호스트에만* 존재. 외부로 나가는 것은:
- LLM API (사용자 BYO 키 → Anthropic 등)
- Git API (push/PR 데이터)
- 외부 텔레메트리 (opt-in)

기본 OFF + opt-in. GDPR/CCPA 대응 자연스러움.

---

## 9.8 사고 / 침해 대응 (M3+)

- 사고 알림 (이메일/Slack) — 비정상 audit 패턴, 인증 실패 폭주, 등.
- 비밀 회수 1-click ("이 토큰을 회수하고 모든 활성 세션 종료").
- 침해 보고 템플릿.
- 사용자별 / 세션별 *킬 스위치*.

---

## 9.9 컴플라이언스 (M4+)

- SOC 2 Type 1/2 — 사내 플랫폼 P3가 들어오기 위해.
- ISO 27001 — 글로벌 엔터프라이즈.
- HIPAA — 의료 도메인 (옵션).
- 컴플라이언스 *증거 수집*은 audit + observability 위에 *별도 모듈*로 구축 (M4+).

---

## 9.10 외부 의존성 보안

| 의존 | 위험 | 대응 |
|---|---|---|
| Anthropic API 다운 | 작업 중단 | 캐시 + Aider 어댑터 폴백 + 사용자 알림 |
| Anthropic 가격 변동 | 비용 추정 어긋남 | 모델별 가격 테이블을 *외부 설정 파일*로 |
| GitHub API rate limit | git/PR 차단 | 사용자 토큰별 쿼터 관리 + webhook 우선 |
| 베이스 이미지 CVE | 컨테이너 컴프로마이즈 | 주간 Renovate + cosign 검증 |
| sysbox-runc 버그 | 격리 우회 | 보안 release 즉시 갱신 채널 |

---

## 9.11 본 문서가 보장하는 인터페이스

1. **owner_id가 없는 도메인 행은 어떤 코드도 생성하지 않는다.** (불변식)
2. **시크릿 평문은 SecretBackend 외부에 존재하지 않는다.** (불변식)
3. **모든 mutate 액션은 audit 이벤트를 발행한다.** (불변식)
4. **에이전트 권한 ≤ 사용자 권한이 *기본*** — owner가 PolicyEngine config로 *명시적 완화* 가능 (§9.2.3).
5. **LLM의 prod 배포·시크릿 CRUD·권한 변경은 *기본 deny*** — PolicyEngine config로 완화 가능, 단 완화는 owner-only + 추가 확인 + audit.
6. **외부 텔레메트리는 기본 OFF, 사용자 opt-in.** (불변식)
7. **인증/시크릿/감사/관측은 각각 어댑터 인터페이스 뒤에** — 직접 의존 금지.
8. **호스트 docker 소켓 마운트는 PolicyEngine으로도 풀 수 없다** — 코드 강제 불변식 (§9.2.4).

이 보장들 위에서 [10](10_tech_stack_decisions.md)이 *왜 특정 기술을 선택했는가*를 결정 매트릭스로 정리한다.
