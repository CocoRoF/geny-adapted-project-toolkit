# M1-E4 Progress — 통합 / Dogfood / Geny 어댑트

[Plan card](../../plan/m1/e4_integration_dogfood_geny.md) · 12 cycle · 10 작업일 estimate.

## 진입 조건 검증

- [x] M1-E1 backend foundation 완료 (b9... 라인업)
- [x] M1-E2 agent + git + sessions 완료 (8ece16f)
- [x] M1-E3 web IDE shell 완료 (5e1fe93)
- [ ] 사용자 prod 서버 정의 (VPS SSH 키) — Cycle 4.10 진입 시 필요
- [ ] 사용자 Geny repo GitHub OAuth — Cycle 4.11 진입 시 필요

## 시작 시점 인벤토리

**서버 (M1-E3 종료):**
- D1 Project / D2 Auth / D4 Sandbox / D7 Secret Vault / D8 Audit / Workspace lifecycle
- ProjectAwareSessionManager + HookRunner (policy + audit + cost)
- PolicyEngine 골격 (단일 계층 default bundle)
- GithubProvider (gh CLI driver) — list_workflow_runs / get_workflow_run_logs 등 surface 있음
- `/api/projects/:pid/audit` (Cycle 3.13)
- 255 server tests · 104 runtime tests

**클라이언트:**
- React Router + Auth + I18n + Theme + Palette + dockview shell + Monaco + FileTree + ChatPanel + DiffCard + ToolCallCard + CostModal + GuardRejectedAlert + AuditPanel + PreviewPanel
- 76 web tests · PWA + 번들 분할

**M1-E4 진입 전 deferred 항목** (M1-E3 마무리에서 누적):
1. xterm.js 터미널 (Cycle 3.7) — backend PTY/WS endpoint 필요
2. CI / Logs 패널 (Cycle 3.13 일부) — backend GitHub Actions + log streaming
3. GitHub Device Flow modal wizard (Cycle 3.2) — `/api/integrations/github/*`
4. backend layout 영속 — server endpoint
5. git status dot / 컨텍스트 메뉴 — git status endpoint
6. Approve/Deny pre-apply — PolicyEngine REQUIRE_USER_APPROVAL UI flow
7. `@file` / `@tool` 자동완성 — file tree query mode
8. recharts 일별 그래프
9. shadcn/ui + Tailwind 디자인 리뉴얼

→ M1-E4 가 이 중 (3) (4) (6) (7) 을 자연스럽게 흡수.

## Cycle 진행 로그

### Cycle 4.1 — DeployTarget 어댑터 3종 (✅ 완료 — *this commit*)

[plan §4.1](../../plan/m1/e4_integration_dogfood_geny.md#cycle-41-——-deploytarget-어댑터-3종-2-pr).

**의존성 추가:** `asyncssh>=2.18` (서버 deploy SSH 채널).

**구성 (5 module + 3 test, 12 case):**
- `server/src/gapt_server/domains/deploy/protocol.py` — `DeployTarget` Protocol (`deploy / status / rollback`) + 값 타입 `DeployRequest`, `DeployContext`, `DeployResult`, `DeployStatus`, `RollbackResult`, `DeployStatusKind` (PENDING/RUNNING/SUCCESS/FAILED/ROLLED_BACK), `DeployTargetError` (stable code suffix).
- `domains/deploy/local.py` — `LocalComposeTarget`:
  - injectable `ComposeRunner` (default = `asyncio.create_subprocess_exec`)
  - per-run state (`_runs`) 가 prior image digests snapshot 보관 → rollback 시 digest 복원
  - 시퀀스: `docker compose ps --format json` (snapshot) → `compose pull` → `compose up -d --remove-orphans`
  - exec_code: `deploy.compose_pull_failed`, `deploy.compose_up_failed`, `deploy.rollback_failed`
  - `finally: env.zeroize` — 시크릿 dict 평문 폐기
- `domains/deploy/ssh.py` — `RemoteSshTarget`:
  - `SshConnectionSpec` (host / user / port / private_key_pem / known_hosts)
  - 기본 runner 가 `asyncssh.connect` (lazy import) + in-memory key load — host disk 미터치
  - 시크릿 env: `KEY=quoted_val command` prefix 패턴 (`SendEnv` 의존 안 함)
  - exec_code: `deploy.ssh.{no_key, bad_key, spec_missing, transport, compose_pull_failed, compose_up_failed, rollback_failed}`
- `domains/deploy/webhook.py` — `WebhookTarget`:
  - `httpx.AsyncClient` POST 에 `X-GAPT-Signature: hex(HMAC-SHA256(secret, body))` 헤더
  - body 에 `env_keys` 만 (시크릿 *값* 절대 POST 안 됨 — 외부 webhook 신뢰 경계 명확)
  - 응답 `{"status": "success" | "failed", ...}` 파싱 → DeployStatusKind 매핑
  - exec_code: `deploy.webhook.{transport, http_{status}, reported_failure, spec_missing}`

**테스트 (12 case):**
- `test_local.py` (4): pull→up 시퀀스, pull 실패 → exec_code, status PENDING (unknown run), env zeroize after deploy
- `test_ssh.py` (3): pull+up runner 호출 + DB_URL env 통과, spec missing → DeployTargetError, runner transport raise → status=FAILED with exec_code
- `test_webhook.py` (5): HMAC signature 검증 + env values 절대 POST 안 됨, HTTP 502 → exec_code, reported failed → exec_code, spec missing → raise, rollback action POST

**Gate:** ruff/mypy clean (5 src), 267 server tests pass (+12), openapi 영향 없음 (라우터 추가는 Cycle 4.2).

**🧪 사용자가 직접 테스트할 수 있는 부분**: 아직 없음. 라우터 미존재 — Cycle 4.2 가 `POST /api/environments/{env_id}/deploy` 추가하면 curl 으로 테스트 가능.

#### Plan 카드 대비 변경

- **단명 ssh-agent → in-memory key load**: plan 의 "단명 ssh-agent" 명시. asyncssh 가 `import_private_key` 로 PEM 을 메모리에 직접 로드 — agent 프로세스 spawn 불필요. 동일한 보안 속성 (host disk 평문 미저장) 더 단순한 구현.
- **secrets via prefix vs SendEnv**: plan 명시 없음. `SendEnv` 는 sshd 의 `AcceptEnv` whitelist 필요 → 호스트 admin 권한 가정 못함. 명령 prefix (`KEY=val cmd`) 가 모든 sshd 에서 동작 + shlex.quote 로 injection 방지.
- **WebhookTarget body 가 env values 미포함**: 의도적. webhook URL 은 외부 신뢰 경계 — secret 평문 POST 시 webhook owner 가 그것을 로깅하거나 leak 할 수 있음. `env_keys` 만 hint 로 보내고 webhook 이 *자기 secret store* 에서 fetch 한다는 명확한 책임 분리.
- **rollback snapshot 기반**: plan 의 "rollback(to: Version)" 시그너처. LocalComposeTarget 은 deploy 시점에 image digest snapshot 을 캡처 → rollback 시 그걸 복원 (registry version log 의존 안 함). 단순하고 self-contained.
- **2 PR → 1 PR**: plan 이 2 PR 명시. 3개 어댑터가 같은 Protocol 위에 빌드되어 분할 가치 작음. 함께 ship.
### Cycle 4.2 — Build/Deploy Orchestrator + Deploy API (대기)
### Cycle 4.3 — CI 결과 polling + UI 통합 (대기)
### Cycle 4.4 — Caddy subdomain 동적 등록 (대기)
### Cycle 4.5 — PolicyEngine 4계층 config (대기, 2 PR)
### Cycle 4.6 — Audit Dashboard (대기)
### Cycle 4.7 — 비용 대시보드 + OTel + Prometheus (대기, 2 PR)
### Cycle 4.8 — 알림 (대기)
### Cycle 4.9 — 헤드리스 oneshot API (대기)
### Cycle 4.10 — Dogfood: GAPT에 GAPT 등록 (대기)
### Cycle 4.11 — Geny 첫 어댑트 (M1 마지막 게이트) (대기)
### Cycle 4.12 — M1 종합 검증 + 사용자 검토 (대기)

## DoD 진행

[Plan 카드](../../plan/m1/e4_integration_dogfood_geny.md) DoD 8 개:

- [ ] `LocalComposeTarget` + `RemoteSshTarget` + `WebhookTarget` 동작 (4.1)
- [ ] prod 배포 2FA TOTP 필수 (4.2)
- [ ] CI 결과 라이브 표시 (4.3)
- [ ] PolicyEngine config override 4계층 + UI 편집 + audit (4.5)
- [ ] Audit dashboard (4.6)
- [ ] OTel + Prometheus exporter (4.7)
- [ ] 🎯 Dogfood: GAPT 가 GAPT 유지보수 (4.10)
- [ ] 🎯 Geny 첫 어댑트: 외부 IDE 0회 (4.11)

## 사용자 검증 게이트

각 cycle 의 ship 마다 *사용자가 직접 테스트 가능한 surface* 가 생기면 progress 카드에 명시. M1-E4 의 핵심 검증은 **4.10 dogfood + 4.11 geny adapt** — 둘 다 사용자가 운영 환경에서 직접 실행.

## Drift (cycle 종료 시 누적 기록)

*(아직 종료되지 않음)*
