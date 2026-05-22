# 11. 로드맵 (Roadmap)

> **상위**: [00](00_overview.md) ~ [10](10_tech_stack_decisions.md)
> **다음**: [12_geny_case_study.md](12_geny_case_study.md)

이 문서는 GAPT의 **단계별 마일스톤**을 정의한다. M0(docs-first 현재) ~ M5(에이전트 자동 운영) 단계별로 *무엇이 완성되면 다음 단계로 넘어가는지*, *각 단계의 위험과 검증 게이트*, *비-목표가 언제 풀리는지*를 명시한다.

---

## 11.1 로드맵 한 페이지

| 단계 | 기간 | 주제 | 페르소나 | 1차 결과물 |
|---|---|---|---|---|
| **M0** | 진행 중 (2026-05~) | docs-first, PoC | (사용자 본인) | 본 docs 12편 + 격리 PoC + bootstrap script |
| **M1** | M0 + 4~6주 | 첫 통합 워크플로 | P1 | 자기 도그푸드 가능, Geny 첫 어댑트 |
| **M2** | M1 + 6주 | 멀티 프로젝트 + 워크트리 + UI 다듬기 | P1 | 일상 사용 도구 |
| **M3** | M2 + 8주 | 멀티 사용자 + OIDC + 옵션 모듈 | P1+P2 | 소규모 팀 사용 가능 |
| **M4** | M3+ | K8s 백엔드, 엔터프라이즈 인터페이스 | P2+P3 | 사내 플랫폼 후보 |
| **M5** | M4+ | 자동 운영, 비즈니스 모델 (옵션) | P3+P4 | (옵션 — 결정 미루기) |

각 단계의 *진입 조건*과 *완료 조건(Definition of Done)* 을 아래 절에서 상세히.

---

## 11.2 M0 — docs-first / PoC

### 진입 조건
- 사용자가 본 프로젝트 시작 결정 (✅ 2026-05-22)

### 작업 항목

1. **분석 docs 12편 작성** (현재 진행 중, 본 문서 포함).
2. **격리 PoC** (`poc/sysbox-bootstrap`):
   - 호스트에 sysbox-runc 설치 검증 script.
   - **SeaweedFS 단일 노드 부팅** (master+volume+filer 1프로세스) + Sysbox 컨테이너에서 SeaweedFS 볼륨 마운트(`/workspace`) 동작 검증.
   - 단일 Sysbox 컨테이너 부팅, 그 안에서 inner dockerd + git clone (SeaweedFS 위) + compose up 동작 검증.
   - 격리 검증 시나리오 I1~I9 (→ [06](06_isolation_and_runtime.md)) 자동 테스트.
   - **SeaweedFS 위 git/compose 성능 측정** (FUSE overhead) — 한계 시 *어떤* 디렉토리만 SeaweedFS인지 다듬는 근거 수집.
3. **에이전트 엔진 통합 PoC** (`poc/agent`):
   - **geny-executor 2.1.0+ PyPI 의존**.
   - `gapt_default.json` manifest 첫 버전 + `Pipeline.from_manifest_async(manifest, credentials)` 호출로 단일 세션 부팅.
   - `claude_code_cli` provider + `claude` CLI OAuth subscription 또는 ANTHROPIC_API_KEY, Hello World 응답.
   - **MCP stdio bridge 1개** (`gapt.agent.mcp_bridge.server`) — 호스트 데몬으로 RPC하는 ~150 LoC 스크립트, Geny `geny_mcp_bridge.py` 패턴.
   - `extras["mcp_config"]`로 CLI에 attach → `mcp__gapt__gapt_read` 같은 tool로 CLI의 LLM이 호스트 도구 호출 검증.
   - `HookRunner` + `PRE_TOOL_USE` 더미 훅으로 audit 이벤트 1개 발행.
4. **데이터 모델 마이그레이션 v1** (`migrations/0001_init.sql`):
   - User / Org / Project / Workspace / Sandbox / AgentSession / Secret / AuditEvent 7개 테이블.
   - owner_id 모든 행에.
5. **모노레포 / CI / 릴리스 체계** 셋업:
   - `gapt-server` (Python wheel + Docker image)
   - `gapt-web` (Vite/React 빌드)
   - `gapt-runtime` (Docker base + toolkit-agent)
   - GitHub Actions: lint/test/build/publish.

### Definition of Done (M0 → M1)

- [ ] 12편 docs 모두 작성, 사용자 검토 통과
- [ ] sysbox-bootstrap PoC가 격리 검증 9개 시나리오 통과
- [ ] geny-executor 통합 PoC가 단일 세션 응답
- [ ] 모노레포 + CI 그린
- [ ] 사용자(P1)가 PoC를 자기 서버에 직접 띄워봄

### 리스크 (M0)

- 분석 깊이 vs 시작 시점의 균형 — 분석에 너무 오래 머무르면 가설이 검증 안 됨.
- Sysbox 설치가 사용자 호스트 환경에 따라 실패 가능.

---

## 11.3 M1 — 첫 통합 워크플로 (Tracer Bullet)

### 진입 조건
- M0 DoD 모두 충족

### 주제
**자기 자신을 호스팅하고 Geny를 첫 어댑트한다.** 가장 가는 *세로 슬라이스* — 모든 레이어가 *최소한* 동작해야 진입.

### 작업 항목

1. **Toolkit Backend** (FastAPI + ARQ + **PostgreSQL** + Redis + **SeaweedFS** 동봉):
   - Auth (MagicLink), Project CRUD, Workspace CRUD.
   - Agent Session 생성/스트리밍 (SSE) — **`Pipeline.from_manifest_async` 단일 진입**, `gapt_default.json` 1차 manifest.
   - `GaptEnvironmentService` ([04](04_llm_agent_layer.md) §4.7) — manifest resolve + CredentialBundle 구성.
   - `GaptToolProvider` ([04](04_llm_agent_layer.md) §4.4) — git/compose/deploy/preview/pr 도구 (`AdhocToolProvider` 구현).
   - MCP stdio bridge (컨테이너 안에서 stdlib만으로 동작, ~150 LoC).
   - Sandbox 생성/관리 (Sysbox + Docker SDK).
   - Git 어댑터 (GitHub OAuth Device Flow + `gh` CLI).
   - Secret Vault (OS keyring 1차).
   - Audit 이벤트 Postgres append-only (월 파티션) — `exec.*.*` 코드 그대로 매핑.
   - **PolicyEngine on HookRunner** — `PRE_TOOL_USE`에서 `ToolFailure(ACCESS_DENIED)`로 veto, 기본 deny + config 편집 가능 ([09](09_security_authz_observability.md), [[feedback_policy_config_not_hardcode]]).
2. **Toolkit Web** (Vite + React + Monaco + dockview + xterm):
   - 단순 레이아웃 (Tree / Editor / Chat / Terminal / Logs).
   - 채팅 패널 + LLM 스트리밍 표시.
   - 파일 트리 + 단순 편집 + 저장.
   - 터미널 xterm.
   - 워크스페이스 라이프사이클 표시.
3. **Toolkit Runtime** (`gapt/runtime:0.1`):
   - 베이스 이미지 + toolkit-agent + git/gh/docker/python/node.
   - Sysbox + Docker Compose 기반 검증.
4. **첫 통합 시나리오 자동 테스트**:
   - 도그푸드: GAPT가 자기 GitHub 레포에서 PR 머지 후 prod에 deploy.
   - Geny 첫 어댑트: Geny 레포에서 plan→implement→test→PR 1사이클 ([12](12_geny_case_study.md)).
5. **Inner loop**: 사용자 compose에 watch 있으면 동작, 없으면 사용자 명시 토글.
6. **Outer loop**: GitHub Actions polling, 로그 라이브 스트림.
7. **Deploy**: LocalCompose + RemoteSSH 어댑터.

### DoD (M1 → M2)

- [ ] 사용자가 GAPT를 GAPT로 *유지보수*할 수 있다 (도그푸드).
- [ ] 사용자가 Geny에서 1사이클을 *외부 IDE 없이* 완수.
- [ ] 골든패스 G1~G4 ([02](02_use_cases_and_personas.md)) 모두 동작.
- [ ] 격리 검증 시나리오 I1~I9 통과 (CI에 자동화).
- [ ] LLM 비용이 *세션 헤더에 라이브 표시*.
- [ ] 채팅 응답 첫 토큰 latency < (API + 100ms).

### 리스크 (M1)

- **범위 확장 유혹**. M1은 *세로 슬라이스*다. dockview 멋, 멀티 사용자, K8s — 모두 금지.
- LLM 비용 폭주 — 첫 사용 시 사용자가 cap 설정을 잊을 가능성. *명시적 첫 등록 시 cap 묻기*.

---

## 11.4 M2 — 멀티 프로젝트 + 워크트리 + UX 다듬기

### 진입 조건
- M1 DoD

### 주제
**일상 사용 도구로 승격.** 사용자가 *매일* 4개 사이드 프로젝트를 GAPT에서 운영하는 상태.

### 작업 항목

1. **멀티 프로젝트 동시 운영**:
   - 좌측 트리에 여러 프로젝트, 빠른 전환.
   - 각자 독립 sandbox + 비용 집계.
2. **워크트리 1급**:
   - `git worktree add/remove` 자동.
   - 같은 프로젝트 여러 워크스페이스 동시.
   - Compose stack 격리 (포트 자동 할당 + Caddy subdomain).
3. **프리뷰 노출**:
   - Caddy on-demand TLS + 와일드카드 도메인.
   - 외부 공유 토글 (cloudflared opt-in).
4. **UX 다듬기**:
   - dockview 레이아웃 프리셋 4개.
   - Plan/Act 모드 UI.
   - diff 카드 (인라인 / side-by-side).
   - 단축키 + Command Palette.
   - 다크/라이트 토글.
5. **알림**:
   - 토스트, 알림 패널, 옵션 Slack/Discord webhook.
6. **PWA 기본**:
   - manifest, service worker, 폰에서 보조 사용 가능.

### DoD (M2 → M3)

- [ ] 사용자가 4개 프로젝트를 일주일 운영, 데스크탑 Cursor 의존도 50% 이하.
- [ ] 골든패스 G3(다중 워크트리), G5(헤드리스 인터페이스) 동작.
- [ ] 외부 친구가 프리뷰 URL로 사용자 데모 확인 가능 (cloudflared).
- [ ] 채팅 UX가 Cursor에 *나쁘지 않음* 수준 (사용자 자체 평가).
- [ ] 비용/Audit 대시보드 기본.

---

## 11.5 M3 — 멀티 사용자 + OIDC + 옵션 모듈

### 진입 조건
- M2 DoD + (선택) 외부 사용자 한두 명 시도

### 주제
**P2(소규모 팀) 진입.** RBAC, SSO, 시크릿 강화.

### 작업 항목

1. **인증 어댑터 확장**:
   - Authentik(OIDC) 어댑터.
   - SSO 옵션, MFA(TOTP) 1급.
2. **RBAC 완성**:
   - User → Org → Project → Env 4계층 권한 구현.
   - 멤버 초대, role 변경, audit.
3. **Secret 백엔드 확장**:
   - Infisical 어댑터 (셀프호스트).
   - SOPS+age 정식 지원.
4. **Audit 강화**:
   - prev_hash 체인.
   - Vector → Loki/외부 SIEM 라우터.
5. **옵션 모듈**:
   - Forgejo 임베드 (옵션).
   - Woodpecker 임베드 (옵션).
   - openvscode-server iframe 보조 모드 (Open VSX 한정).
6. **모델/도구/strategy 확장은 geny-executor 본체에 PR**:
   - 신규 LLM provider, 새 도구 카테고리, 새 메모리 백엔드, 새 sub-agent orchestration 패턴, 새 평가 strategy, 새 guard 등 — *모두 executor에서 일반화* ([04](04_llm_agent_layer.md) §4.11 표).
   - GAPT는 의존 버전 + manifest 편집으로 받음.
   - 별도 어댑터/Protocol 클래스 추가 금지 ([[feedback_extend_executor_not_adapter_layer]]).
7. **신규 manifest 템플릿 추가**:
   - `gapt_planning.json` (Plan 모드 강화), `gapt_review.json` (PR 리뷰), `gapt_headless.json` (cron/webhook).
   - 사용자가 UI로 manifest 복제·편집 (Geny의 EnvironmentService 패턴).
8. **LSP 1차**:
   - pyright / tsserver / gopls 컨테이너 자동 실행.
8. **OS push 알림** (PWA web push).
9. **언어 i18n** (영/한 2개 유지).

### DoD (M3 → M4)

- [ ] 외부 2~3명 사용자 (P2 페르소나) 실제 사용, 멤버 권한 분리 동작.
- [ ] 감사 데이터를 외부 SIEM에 라우팅 검증.
- [ ] Authentik OIDC SSO 통과.
- [ ] (필요 시) geny-executor에 추가 provider 일반화 → 그 새 버전으로 Anthropic 외 모델로 한 세션 완주.

### 비-목표 풀기 (M3에서)
- 사내 Git 호스팅 (Forgejo) — 옵션으로.
- openvscode-server 임베드 — 보조 모드로.

---

## 11.6 M4 — K8s 백엔드 / 엔터프라이즈 인터페이스

### 진입 조건
- M3 + P3 페르소나 수요 신호 (실제 사내 엔지니어가 시도 의지 표명)

### 주제
**P3(사내 플랫폼) 진입.** 멀티 노드 / K8s / 컴플라이언스 준비.

### 작업 항목

1. **SandboxBackend.K8s 구현**:
   - Pod = 컨테이너, namespace 격리, NetworkPolicy.
   - GAPT 컨트롤 플레인 자체도 K8s 배포 가능.
2. **상태 외부화**:
   - PostgreSQL을 외부 매니지드/HA로 이동 (마이그레이션 도구는 단일→HA만).
   - SeaweedFS를 멀티노드 (Filer + Volume Server 분리, replication factor 설정). 코어 영속 파일 추상화는 이미 SeaweedFS라 변화 작음.
3. **데몬 RPC 네트워크**:
   - unix socket → mTLS HTTP.
4. **컨트롤 플레인 HA**:
   - 다중 replica + leader election (Redis or etcd).
5. **GitOps 통합**:
   - ArgoCD/Flux 어댑터.
6. **컴플라이언스 모듈**:
   - SOC 2 증거 수집 자동화.
7. **엔터프라이즈 IDP**:
   - Keycloak, SAML, SCIM provisioning.
8. **ABAC 옵션**:
   - Casbin/Cedar 정책 엔진.
9. **Helm 차트 / 운영 가이드**:
   - OpenHands식 source-available Helm.
10. **레지스트리 옵션**: Distribution v2 셀프호스트.

### DoD (M4 → M5)

- [ ] 50명 규모 가상 시나리오에서 멀티노드 동작 (사내 테스트).
- [ ] K8s 위에서 OOM/장애 복구 시나리오 통과.
- [ ] OIDC + SAML + SCIM 모두 통과.
- [ ] ArgoCD GitOps 사이클 동작.

---

## 11.7 M5 — 자동 운영 + 비즈니스 모델 (옵션)

### 진입 조건
- M4 + 외부 사용자 누적 + 비즈니스 결정

### 주제
**P4 (자동 운영) 도입 + 지속 가능성.**

### 작업 항목

1. **헤드리스 자동화 1급**:
   - Cron 트리거 (`/api/sessions/scheduled`)
   - Slack/Discord 트리거 → 세션 생성 → 결과 회신
   - Webhook 양방향 (GitHub Issue → 세션)
   - 정책 엔진: "auto-PR if green CI" 등
2. **에이전트끼리 협업**:
   - 한 프로젝트 안 멀티 에이전트 (예: planner + executor + reviewer).
   - geny-executor의 SubPipeline + cross-session 통신.
3. **모델 비용 최적화**:
   - 모델 라우터 (단순 작업 → Haiku, 복잡 → Opus).
   - 캐시 적극 활용 (Stage 5).
4. **클라우드 옵션** (만약 비즈니스 결정 시):
   - "GAPT Cloud" (자체 호스팅), 사용자가 셀프호스트 vs 우리 클라우드 선택.
   - 코어 OSS 유지, *호스팅·관리 서비스*가 유료 라인.
5. **마켓플레이스 / 카탈로그** (옵션):
   - 프로젝트 템플릿, MCP 서버 큐레이션 카탈로그.

### DoD (M5 안정)

- [ ] 외부 사용자 베이스 (수십 명 이상) 6개월 안정 운영.
- [ ] 자동화 시나리오 (cron/webhook → 자동 PR) 사용자 N명이 매일 사용.
- [ ] 모델 비용을 사용자 측에서 *50% 절감* 가능한 라우터.

---

## 11.8 비-목표가 풀리는 시점

| 비-목표 (00 문서) | 풀리는 단계 | 조건 |
|---|---|---|
| 신규 앱 생성 (Lovable/v0) | (영구 비-목표) | — |
| 로컬 IDE 확장 | (영구 비-목표) | — |
| 자체 모델 학습 | (영구 비-목표) | — |
| K8s/멀티 노드 | M4 | P3 수요 신호 |
| 사내 Git 호스팅 (Forgejo) | M3 옵션, M4 필수 옵션 | — |
| 자체 모델 카탈로그 | M5 (옵션) | 비즈니스 결정 |
| 데스크탑 앱 | (영구 비-목표) | — |
| 블록체인 등 | (영구) | — |

---

## 11.9 단계별 위험 매트릭스

| 단계 | 최대 위험 | 완화 |
|---|---|---|
| M0 | 분석 마비, *시작 지연* | docs-first 4주 cap, 부족하면 build로 |
| M1 | 세로 슬라이스가 *모든 레이어*에 닿으므로 디버깅 폭증 | 격리 PoC를 M0에서 먼저 닫음. tracer bullet 정신 |
| M2 | UX 다듬기 *무한 루프* | 사용자(P1) 본인이 매일 사용 + 명확한 골든패스 통과 게이트 |
| M3 | 멀티 사용자 → 권한/감사 *대량 회귀* 위험 | 모델은 처음부터 owner_id, 마이그레이션 부담 작게 |
| M4 | K8s 도입이 *동일 코드를 깨거나 운영 폭증* | Sandbox 어댑터 인터페이스 견고하게 (M0~M1에 미리) |
| M5 | 비즈니스 모델 *코어 OSS 정신*과 충돌 | 코어 영구 OSS 보장, 클라우드는 *옵션* |

---

## 11.10 의사 결정 로그 (Decision Log) 운영

[[feedback_durable_instructions]] 정신에 따라 *plan/progress* 폴더를 GAPT 자체 레포에도 운영:

```
gapt/
├── docs/             # 본 분석 (12편)
├── analysis/         # 신규 주제 심층 분석 (cycle 단위)
├── plan/             # 진행 중 cycle 계획
├── progress/         # 완료된 cycle 진행 기록
├── src/
├── ...
```

Cycle은 *주제 단위* (예: `cycle_20260601_m1_sandbox`, `cycle_20260615_m1_agent_loop`). 각 cycle:
- `analysis/{date}_{topic}.md` — 분석
- `plan/{cycle}.md` — 단계별 계획
- `progress/{cycle}.md` — 진행 기록 (매 PR마다 업데이트)

이는 Geny에서 검증된 패턴(MEMORY 참조). 동일 cadence.

---

## 11.11 출시 / 공개 전략 (M1~M2)

- M1 완료 시 *비공개* 사용 (사용자 본인 + 신뢰 grupos).
- M2 완료 시 *공개 OSS 출시* (GitHub repo, README, 가이드 docs, 데모 영상).
- 공개 채널: HN / r/selfhosted / r/devops / Korean OSS 커뮤니티.
- 메시지: *"Self-host AI DevOps console for solo developers and small teams"*.

---

## 11.12 본 문서가 보장하는 인터페이스

1. **각 단계는 명확한 진입/완료 조건이 있다** — 모호한 "곧 끝남" 금지.
2. **세로 슬라이스 정신** (M1 tracer bullet) — 깊이 한 점부터.
3. **비-목표는 *언제* 풀리는지 명시** — 슬립 영구 비-목표가 아닌 한.
4. **각 단계 끝에 *사용자 확인*이 게이트** — 자가 평가 금지.
5. **plan/progress cadence를 GAPT 자체에 적용** — Geny에서 검증된 패턴.

마지막 [12](12_geny_case_study.md)는 *Geny를 첫 어댑트할 때 구체적 작업 절차*를 케이스 스터디로 정리한다.
