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

### Cycle 4.2 — Build/Deploy Orchestrator + Deploy API (✅ 완료 — *this commit*)

[plan §4.2](../../plan/m1/e4_integration_dogfood_geny.md#cycle-42-——-builddeploy-orchestrator-d6--deploy-api-1-pr).

**구성 (3 module + 2 test, 11 case):**
- `domains/deploy/two_factor.py` — `TwoFactorVerifier` Protocol + `AcceptAnyCodeVerifier` (dev stub) + `AlwaysDenyVerifier` (test) + `TwoFactorError`. 실 TOTP backend 는 `users.totp_secret_encrypted` migration 후 wrap.
- `domains/deploy/orchestrator.py` — `DeployOrchestrator` 가 5단계 시퀀스 실행:
  1. `PolicyEngine.evaluate("deploy.{env_name}", actor=USER, scope)` → DENY 면 OrchestratorError + audit("deploy.denied", DENIED)
  2. `REQUIRE_2FA` → `TwoFactorVerifier.verify(user_id, code)` 실패면 TwoFactorError + audit
  3. `REQUIRE_USER_APPROVAL` → audit("deploy.user_approved") (UI 가 이미 click 후 호출)
  4. `secret_resolver(refs)` → plaintext dict (default = empty; 라우터가 SecretVault 와 wire)
  5. `audit("deploy.start")` → `target.deploy(ctx)` → `audit("deploy.{status}")` (try/finally zeroize env_secrets)
  - `rollback(...)`: 동일 policy + 2FA gate → `target.rollback(ctx, to_version)` → audit("deploy.rollback")
  - `stream_status(...)`: poll `target.status()` until terminal, yield JSON 프레임 — SSE wire 는 다음 cycle.
- `routers/deploy.py` — `POST /api/environments/{env_id}/deploy` + `/rollback`:
  - `_resolve_env` (404), `fetch_project_for` (403 if non-member)
  - `_build_target` (kind → target instance), per-env asyncio.Lock 으로 동시 deploy 직렬화
  - Exception 매핑: TwoFactorError → 412, OrchestratorError(policy_denied) → 403, 그 외 → 500
- `domains/deploy/__init__.py` 가 orchestrator/2FA 타입 모두 re-export. `app.py` 가 `deploy.router` include.

**테스트:**
- `test_orchestrator.py` (6 case): success → start+terminal audit, DENY → OrchestratorError + denied audit + target 미실행, REQUIRE_2FA 코드 없으면 TwoFactorError, REQUIRE_2FA 코드 있으면 통과, secret_resolver 호출 + 시크릿 target 까지 전달, rollback → target.rollback 호출 (to_version 전달)
- `test_routes.py` (5 case, Postgres): happy path (webhook target), webhook 502 → exec_code, 환경 미존재 → 404, 비멤버 → 403, rollback round-trip
- 라우터 fixture 가 `_build_target` 을 monkey-patch 해서 모든 kind → WebhookTarget (`poster` injected) — 실 docker/SSH 미터치

**Gate:** ruff/mypy clean (70 src), 278 server tests (+11), openapi check 통과.

**🧪 사용자가 직접 테스트할 수 있는 부분 — *이제 가능*:**

```bash
# 1. 서버 띄우기 (Postgres + dev DSN 필요)
cd server && uv run uvicorn gapt_server.app:app --host 0.0.0.0 --port 8001

# 2. 매직 링크 로그인 (dev 모드는 서버 콘솔에 callback URL 출력)
curl -X POST http://localhost:8001/api/auth/magic-link \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
# 서버 로그에서 token 찾아 paste:

curl -c /tmp/cookies.txt \
  "http://localhost:8001/api/auth/magic-link/callback?token=PASTE_TOKEN"

# 3. 프로젝트 생성 (이미 가능)
# 4. 환경 생성 (UI 미존재 — DB 직접 insert 필요. UI wizard 는 추후 cycle)

# 5. deploy 호출
curl -b /tmp/cookies.txt -X POST \
  http://localhost:8001/api/environments/{env_id}/deploy \
  -H "Content-Type: application/json" \
  -d '{"version": "v1"}'
# → 200 {"run_id": "...", "status": "success" | "failed", "exec_code": ..., "log": "..."}
```

웹 UI 트리거 (`DeployModal`) 는 추후 cycle 에서. 본 cycle 은 backend HTTP API + 테스트 수준까지.

#### Plan 카드 대비 변경

- **SSE deploy progress stream 미연결**: plan 명시 "진행 로그 SSE". orchestrator 에 `stream_status` 가 yield JSON 프레임 가능하지만 라우터 SSE endpoint 는 다음 cycle. 현재는 `deploy()` 가 동기 호출.
- **5분 내 진행 중 deploy queue 미구현**: plan 의 "queue + 사용자 확인". 본 cycle 은 per-env asyncio.Lock 으로 동시성만 차단 — 두 번째 요청은 첫 번째 완료까지 *block*. "queue + UI confirm" 은 Cycle 4.8 (알림) 와 함께.
- **TOTP backend stub**: `AcceptAnyCodeVerifier` 가 dev/test default. 실 TOTP 는 `users.totp_secret_encrypted` 컬럼 + `pyotp` 추가 + verifier 구현 (별도 cycle). 412 흐름은 이미 wire-up.
- **Environment CRUD UI 부재**: 백엔드 `/api/projects/{pid}/environments` 존재하지만 웹 UI 없음 — M1-E3 deferred 카탈로그에 추가.
- **stream_status JSON shape**: 한 줄 JSON `{"run_id","status","exec_code"}` — Cycle 2.10 의 chat SSE 와 같은 패턴.
### Cycle 4.3 — CI 결과 polling + UI 통합 (✅ 완료 — *this commit*, 스코프 축소)

[plan §4.3](../../plan/m1/e4_integration_dogfood_geny.md#cycle-43-——-ci-결과-polling--ui-통합-1-pr).

**스코프 축소 사유**: plan 의 "ARQ 백그라운드 polling 10s" + "WS `/api/projects/{pid}/ci/stream` 라이브 stream" + "CI 그린 → 채팅에 자동 메시지" 는 ARQ + Redis 가 필요하지만 M1-E1 에서 Redis 의존성을 도입하지 않았음 (M2 로 deferred — 본 progress card 의 누적 drift 참조). 따라서 본 cycle 은 **on-demand GET endpoint + UI panel + manual refresh** 로 축소. SSE 실시간 stream + 채팅 자동 메시지 + GitHub Webhook ingress 는 M2 의 Redis 도입 후 wrap.

**서버 신규 (1 module + 1 setting + 1 test, 4 case):**
- `settings.py` — `ci_github_token: str | None`. M1 의 서버-wide dev 토큰. M2 가 project 별 SecretVault 룩업으로 교체.
- `routers/ci.py` — `GET /api/projects/{pid}/ci/runs?branch=&limit=`:
  - `fetch_project_for` (viewer 이상)
  - `settings.ci_github_token` 미설정 → 412 `ci.no_token` + 운영자 친화 메시지
  - `parse_github_repo(project.git_remote_url)` (HTTPS / SSH / bare 형태 지원) — 파싱 실패 → 412 `ci.repo_unparseable`
  - `GithubProvider(token, repo).list_workflow_runs(branch, limit)`
  - `GitOperationError` → 502
- `tests/ci/test_routes.py` (4 case): parser 4 variant, happy path (stub runner), 토큰 미설정 → 412, 비멤버 → 403
- 서버 282 pass (+4), openapi 갱신 (`/api/projects/{pid}/ci/runs` 추가).

**웹 신규 (2 module + 1 test, 3 case):**
- `src/api/ci.ts` — `CiRun`, `WorkflowRunStatus`, `listCiRuns(pid, {branch?, limit?})`
- `src/ci/CiPanel.tsx` — 5 컬럼 테이블 (workflow / branch / status / SHA[:7] / link), 브랜치 필터 input + Refresh 버튼, `ci.no_token` 에러는 `data-error-code` 속성으로 surface (UI 가 추후 친화 메시지로 분기 가능).
- `src/ide/panels.tsx` 에 `<CiPanelDock>`, `DockviewShell.tsx` components 에 `ci: CiPanelDock` 등록.
- i18n 19 키 추가 (en/ko): ci.title / loading / empty / refresh / branch / col.* (5) / status.* (7).

**테스트 (`tests/CiPanel.test.tsx`, 3 case):**
- 정상 응답 → 테이블 + 7자 SHA 표시
- 빈 응답 → empty-state
- 412 ci.no_token → role="alert" + data-error-code

**Gate:** server ruff/mypy clean, 282 server pass (+4), openapi up to date. Web lint/typecheck/format clean, 79 web test pass (+3), build 성공 (PWA precache 773 KiB).

**🧪 사용자가 직접 테스트할 수 있는 부분 — *이제 가능*:**

```bash
# 1. GitHub 토큰 설정 (dev/test 한정)
export GAPT_CI_GITHUB_TOKEN="ghp_yourtoken"

# 2. 서버 띄우기
cd server && uv run uvicorn gapt_server.app:app --port 8001

# 3. 매직 링크 로그인 → 프로젝트 생성 (Cycle 4.2 와 동일 흐름)
#    git_remote_url 은 `https://github.com/owner/repo.git` 형식 필요.

# 4. CI 호출
curl -b /tmp/cookies.txt \
  "http://localhost:8001/api/projects/{pid}/ci/runs?branch=main&limit=10"
# → 200 [{"id": 123, "name": "CI", "head_branch": "main", "status": "completed_success", ...}, ...]

# 5. 웹 UI 에서: 프로젝트 워크스페이스 진입 → 우측 패널 영역에 CI 패널 드래그 (custom 레이아웃)
# (현재 review/debug preset 의 default panel slot 에 CI 미배치 — Cycle 4.6/4.8 의 dashboard 합류 시 재배치 예정)
```

#### Plan 카드 대비 변경 (스코프 축소 명시)

- **ARQ background poller 미구현**: plan 의 "활성 워크스페이스의 PR 브랜치에 대해 10s polling". M1-E1 에서 Redis 의존성 도입 안 했음 → ARQ 사용 불가. M2 의 Redis 도입 cycle 에서 wrap.
- **`WS .../ci/stream` 미구현**: plan 의 "진행 + 결과 WebSocket stream". 동일하게 ARQ/Redis 의존. 본 cycle 은 manual `Refresh` 버튼.
- **CI 그린 → 채팅 자동 메시지 미구현**: poller 가 없어 자동화 불가. M2 에서.
- **GitHub Webhook ingress (`POST /api/integrations/github/webhook`) 미구현**: HMAC 검증 자체는 Cycle 4.1 의 `WebhookTarget` 에 패턴 있지만 ingress 라우터는 별도. 사용자 호스트가 외부 도달 가능한 경우만 의미 — M1 dogfood 단계는 manual polling 으로 충분.
- **per-project SecretVault token lookup 미구현**: `settings.ci_github_token` 서버-wide dev 토큰만. 멀티 프로젝트 / 멀티 owner 시나리오는 `projects.git_auth_secret_ref` 룩업 추가 (별도 cycle).
- **review/debug preset 에 CI 패널 자동 배치 안 함**: Cycle 3.13 에서 review preset 의 "ci" slot 을 audit panel 로 교체했음. CI 패널은 dockview component 로 등록되어 있어 사용자가 *custom layout* 으로 드래그 가능. 자동 배치는 dashboard cycle (4.6/4.7) 와 함께 재구성 예정.
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
