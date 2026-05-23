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
### Cycle 4.4 — Caddy subdomain 동적 등록 (✅ 완료 — *this commit*)

[plan §4.4](../../plan/m1/e4_integration_dogfood_geny.md#cycle-44-——-caddy-subdomain-동적-등록-1-pr).

**구성 (4 module + 1 router + 3 test, 18 case):**
- `domains/caddy/admin_api.py` — `CaddyAdminClient` (`get/put/post/delete`) + `CaddyHttpTransport` (httpx 기반, 5s 타임아웃) + `CaddyAdminError` (stable code suffix `caddy.admin.{get,put,post,delete}_failed` / `server_error`). Transport 가 protocol — 테스트는 hand-rolled async callable 주입.
- `domains/caddy/subdomain.py` — `SubdomainManager.register(binding) / unregister(slug) / list_routes()`:
  - 라우트 페이로드: `@id`=`gapt-workspace-{slug}` (DELETE 시 `/id/{route_id}` 패턴으로 정확 1개 노드 타겟), `match.host`=`{slug}.{preview_domain}`, `handle.reverse_proxy.upstreams.dial`=`{host}:{port}`
  - POST `/config/apps/http/servers/preview/routes/...` (Caddy 의 append-array 시맨틱), DELETE `/id/{route_id}`
  - 404 on DELETE = 멱등 no-op (이미 없어진 라우트 재요청 안전)
- `domains/caddy/share.py` — `issue_share_link / parse_share_link`:
  - 형식: `{workspace_id}.{expiry_unix}.{hex_signature}`
  - 서명: `HMAC-SHA256(secret, "{workspace_id}.{expiry}")`
  - `hmac.compare_digest` 로 constant-time 비교 (timing side channel 방지)
  - 에러 코드: `share.{invalid_ttl, malformed, bad_signature, expired}`
- `routers/preview.py` — 3 endpoint:
  - `POST /api/workspaces/{wid}/preview {upstream_host, upstream_port}` → 201 + `{host, workspace_id}` (Caddy 미설정 시 412 `preview.disabled`)
  - `DELETE /api/workspaces/{wid}/preview` → 204 (Caddy 없어도 멱등 200)
  - `POST /api/workspaces/{wid}/share?ttl_s=` → `{token, url, expires_in_s}` (`ttl_s > share_link_max_ttl_s` 면 400 `share.ttl_too_long`)
  - 모든 endpoint `fetch_project_for` 멤버십 게이트 (403 if non-member)
- `settings.py` — `caddy_admin_url`, `caddy_preview_domain`, `share_link_secret`, `share_link_max_ttl_s` (24h default).
- `app.py` 가 `preview.router` include.

**테스트 (18 case):**
- `test_admin_and_subdomain.py` (7): GET 200/404, PUT 500 → CaddyAdminError, DELETE 404 swallow, register POST 페이로드 검증 (@id / host / dial), unregister /id/{slug} 타겟, unregister 404 멱등
- `test_share.py` (5): round-trip, malformed token, bad signature (1 hex flip), expired, ttl ≤ 0
- `test_routes.py` (6): register → Caddy POST, unregister → Caddy DELETE, Caddy 미설정 → 412 preview.disabled, share round-trip + URL 형식, ttl cap → 400, 비멤버 → 403

**Gate:** ruff/mypy clean (76 src), 300 server tests (+18), openapi up to date.

**🧪 사용자가 직접 테스트할 수 있는 부분 — *Caddy 설정 시 가능*:**

```bash
# 1. Caddy 설정 (compose 또는 별도)
# docker compose up caddy  # admin :2019, on-demand TLS, server "preview" 정의 필요
# (compose/Caddyfile 의 정비는 Cycle 4.10 dogfood 와 함께)

export GAPT_CADDY_ADMIN_URL="http://localhost:2019"
export GAPT_CADDY_PREVIEW_DOMAIN="preview.localhost.dev"
export GAPT_SHARE_LINK_SECRET="$(openssl rand -hex 32)"

# 2. 서버 부팅 + 로그인 + 워크스페이스 생성 (이전 cycle 흐름)

# 3. 프리뷰 등록
curl -b /tmp/cookies.txt -X POST \
  http://localhost:8001/api/workspaces/{wid}/preview \
  -H "Content-Type: application/json" \
  -d '{"upstream_host": "10.0.0.5", "upstream_port": 3000}'
# → {"host": "01k....preview.localhost.dev", "workspace_id": "01K..."}

# 4. 브라우저: https://01k....preview.localhost.dev/
#    (Caddy on-demand TLS 가 자체서명 또는 Let's Encrypt 발급)

# 5. 외부 공유 링크 발급
curl -b /tmp/cookies.txt -X POST \
  "http://localhost:8001/api/workspaces/{wid}/share?ttl_s=3600"
# → {"token": "01K...12345.abcdef...", "url": "https://01k.../?share=...", "expires_in_s": 3600}
```

#### Plan 카드 대비 변경

- **on-demand TLS 설정 미포함**: plan 의 "on-demand TLS" — Caddy 자체 설정. 본 cycle 은 admin API 만 wire (라우트 등록/해제). on-demand TLS 의 `ask` endpoint (등록된 워크스페이스만 cert 발급 허용) 는 Caddy 설정 + 별도 endpoint 필요 — Cycle 4.10 dogfood 의 compose 정비 단계에서 추가.
- **사용자 SSO 인증 게이트 미연결**: plan 의 "M1-E1 세션 cookie 검증 — 기본". Caddy forward_auth 또는 reverse_proxy 의 인증 middleware 가 필요. 본 cycle 은 워크스페이스 멤버만 *프리뷰를 register* 할 수 있음 — *접근* 시 게이트는 Caddy 측 설정.
- **공유 토글 UI 미연결**: backend `share` endpoint 만. PreviewPanel (Cycle 3.12) 에 share 버튼 추가는 추후 cycle / dogfood 단계.
- **Caddy 설정 자체는 별도**: 서버는 admin API 호출만 함. Caddyfile / Caddy JSON config 의 `apps.http.servers.preview` server 정의는 운영자가 미리 부팅해야 함 — `docs/operations/install.md` (Cycle 4.12) 에 가이드 예정.
- **routes_path 의 `...`**: Caddy 의 array append 시맨틱 — `/routes/...` 가 array 끝에 push. 직접 index 지정 (`/routes/0`) 보다 안전.

### Cycle 4.5 — PolicyEngine 4계층 config (✅ L1 + L2 완료 — *this commit*, L3/L4 deferred)

[plan §4.5](../../plan/m1/e4_integration_dogfood_geny.md#cycle-45-——-policyengine-config-시스템-——-4계층-2-pr).

**스코프 축소 사유**: plan 의 4계층 (L1 built-in / L2 server YAML / L3 org DB / L4 project DB + `.gapt/policy.yaml`) 중 L3/L4 는 Alembic migration (`org_policies` / `project_policies`) + PUT API 가 필요. 본 cycle 은 **L1 + L2 + 불변식 강제 + 효과 정책 조회 API** 까지. L3/L4 + PUT 편집은 별도 cycle.

**의존성 추가:** `pyyaml>=6.0`, `types-pyyaml` (dev).

**구성 (3 module + 1 router + 3 test, 13 case):**
- `policy/config_loader.py`:
  - `INVARIANT_FLOORS` — 5 액션의 최대 허용 완화 수준: `deploy.prod` (REQUIRE_2FA), `secret.create/update/delete` (REQUIRE_USER_APPROVAL), `git.push.force` (DENY)
  - 순서: ALLOW < REQUIRE_USER_APPROVAL < REQUIRE_2FA < DENY
  - `check_invariant(action, decision)` — floor 보다 *느슨* 시 `PolicyConfigError("policy.config.invariant_violated")`. 더 엄격은 허용.
  - `load_yaml(path)` / `parse_dict(raw)` — short form `action: decision_str` + long form `action: {decision, reason}` 모두 지원. 파싱 시점에 invariant 체크.
- `policy/engine.py`:
  - `PolicyEngine.__init__(audit_sink, overrides, override_reasons)` — `overrides: dict[str, PolicyDecision]` 가 L2 결과
  - `evaluate()` 가 overrides 먼저 lookup, 없으면 `_DEFAULTS`
  - `effective_table()` — `[{action, decision, source: "server"|"builtin", reason}]` 반환
- `settings.py` — `policy_config_path: str | None` (`GAPT_POLICY_CONFIG_PATH`)
- `container.py` — `_engine_from_settings(settings, audit)` 가 startup 시 YAML 로드 + invariant 검증 + PolicyEngine 생성. 잘못된 config 는 startup 시점에 raise.
- `routers/policies.py` — `GET /api/policies` (인증 필수): `{rows, invariants}`

**테스트 (13 case):**
- `test_config_loader.py` (7): 누락 파일 → 빈 set, short/long form, invariant raise (ALLOW for deploy.prod / git.push.force), 미지 decision, INVARIANT_FLOORS 커버
- `test_layered_engine.py` (4): override 우선, override 없으면 builtin, agent forbidden 이 override 보다 우선, effective_table source 라벨링
- `test_routes.py` (2): YAML 적용된 효과 테이블, 비인증 → 401

**Gate:** ruff/mypy clean (78 src), 313 server tests (+13), openapi 갱신.

**🧪 사용자가 직접 테스트할 수 있는 부분 — *이제 가능*:**

```bash
# 1. 정책 YAML 작성
cat > /tmp/gapt-policies.yaml <<'EOF'
actions:
  git.push.protected:
    decision: allow
    reason: "local CI is the gate"
EOF

# 2. 서버 부팅 + 정책 조회
export GAPT_POLICY_CONFIG_PATH=/tmp/gapt-policies.yaml
cd server && uv run uvicorn gapt_server.app:app --port 8001

curl -b /tmp/cookies.txt http://localhost:8001/api/policies | jq
# rows[git.push.protected] = {source: "server", decision: "allow", reason: "..."}
# rows[secret.create]      = {source: "builtin", decision: "deny"}
# invariants               = {"deploy.prod": "require_2fa", ...}

# 3. 잘못된 YAML (invariant 위반):
echo 'actions: {deploy.prod: allow}' > /tmp/bad.yaml
GAPT_POLICY_CONFIG_PATH=/tmp/bad.yaml uv run uvicorn gapt_server.app:app
# → PolicyConfigError on startup (서버 부팅 실패)
```

#### Plan 카드 대비 변경

- **L3 (org DB) + L4 (project DB + `.gapt/policy.yaml`) deferred**: Alembic migration + scope 별 PUT API + diff UI 가 별도 cycle. 본 cycle 의 `PolicyEngine.overrides` dict + `effective_table` source 라벨이 L3/L4 wrap 시 그대로 사용됨.
- **PUT API 미구현**: L3/L4 없으면 PUT 대상 없음.
- **5 invariant floor**: plan 의 "5개 코드 강제 불변식" → `INVARIANT_FLOORS`. 단순 deny 강제가 아니라 *floor* 모델 (operator 가 *더 엄격하게* 만드는 건 항상 가능).
- **YAML 핫 리로드 미구현**: startup-only. SIGHUP handler / file watcher 는 M2.
- **변경 diff UI 미구현**: PUT 없으니 diff UI 도 없음.
### Cycle 4.6 — Audit Dashboard (✅ 완료 — *this commit*)

[plan §4.6](../../plan/m1/e4_integration_dogfood_geny.md#cycle-46-——-audit-dashboard-1-pr).

**구성 (서버 1 router 확장 + 4 test, 웹 2 module 확장 + 2 test, i18n 12 키):**

서버:
- `routers/audit.py` — `GET /api/projects/{pid}/audit/export?format=csv|jsonl[&action_prefix&outcome&since&until]`:
  - `_EXPORT_MAX = 5000` (서버 캡 — 큰 export 도 bounded)
  - `_CSV_FIELDS` 11 컬럼 (id/ts/actor_type/actor_id/action/outcome/duration_ms/exec_code/scope/subject/payload). scope/subject/payload 는 `json.dumps` 직렬화 → CSV 필드 안에서도 안전.
  - JSONL 은 `models.AuditEvent` 한 줄 = 한 row.
  - `fetch_project_for` 멤버 게이트 (403 if non-member)
  - 응답: `StreamingResponse(content_type="text/csv" | "application/x-ndjson", Content-Disposition: attachment; filename=...)` — 브라우저가 곧장 다운로드.
  - 동일 endpoint 가 `list_project_audit` 와 같은 필터 (action_prefix / outcome / since / until) 를 공유.
- `tests/audit/test_routes.py` (+4 case): CSV round-trip (header + 3 rows), JSONL round-trip (2 rows, each JSON parseable), action_prefix=test.event.1 → 1줄, 비멤버 → 403.

웹:
- `src/api/audit.ts` — `exportProjectAuditUrl(projectId, format, query)` 가 export URL 빌더. UI 가 fetch 가 아니라 `<a href download>` 로 브라우저 다운로드 트리거.
- `src/audit/AuditPanel.tsx` 전면 개편:
  - 시간 범위 preset (오늘 / 최근 7일 / 최근 30일 / 전체 / custom datetime-local 2개)
  - `resolveRange(preset, custom)` 유틸이 ISO since/until 계산
  - `PAGE_SIZE = 100`, offset paginate. 마지막 페이지가 100 미만 → Load more 버튼 자동 숨김 (`hasMore` state)
  - CSV / JSONL 다운로드 앵커 (`<a download>`) — 현재 필터 그대로 적용한 URL
  - `baseQuery` 메모이즈 (action_prefix / outcome / since / until) → refresh / loadMore / export 가 동일 쿼리 공유
- i18n 12 키 추가 (en + ko): audit.filter.since/until, audit.export.csv/jsonl, audit.load_more, audit.range.{label,today,7d,30d,all,custom}.
- `tests/AuditPanel.test.tsx` (+2 case): export anchor href 가 action_prefix 반영, 100-row 응답 → Load more 클릭 → offset=100 호출 + 2번째 페이지 단행 → 버튼 사라짐.

**Gate:** server ruff/mypy clean, 317 server tests pass (+4), openapi 갱신. Web typecheck / lint / format clean, 81 web tests pass (+2), build 성공 (PWA precache 776 KiB).

**🧪 사용자가 직접 테스트할 수 있는 부분 — *이제 가능*:**

```bash
# 1. 서버 부팅 + 매직 링크 로그인 (이전 cycle 흐름)
# 2. 프로젝트 생성 + 약간의 audit 이벤트 발생 (chat/edit/git push 등)

# 3. CSV 다운로드
curl -b /tmp/cookies.txt \
  "http://localhost:8001/api/projects/{pid}/audit/export?format=csv&action_prefix=agent." \
  -o audit.csv
head -3 audit.csv
# id,ts,actor_type,actor_id,action,outcome,duration_ms,exec_code,scope,subject,payload
# ...

# 4. JSONL 다운로드
curl -b /tmp/cookies.txt \
  "http://localhost:8001/api/projects/{pid}/audit/export?format=jsonl&outcome=error&since=2026-05-01T00:00:00Z" \
  -o audit.jsonl

# 5. 웹 UI: 프로젝트 워크스페이스 → audit 패널
#    - 시간 범위 select 에서 "최근 30일" / "custom" 선택
#    - "Export CSV" / "Export JSONL" 버튼 클릭 → 브라우저 다운로드
#    - 100개 이상 결과면 "Load more" 버튼 출현 → 클릭하면 추가 100개 append
```

#### Plan 카드 대비 변경

- **Subject diff viewer 부재**: plan 의 "subject before/after diff (JSON viewer)". 현재는 단순 텍스트 컬럼만. JSON diff 컴포넌트는 다음 cycle 또는 dogfood 단계에서 패널 확장.
- **per-actor pivot 미구현**: plan 의 "특정 사용자/세션 행위 시간순". 현재는 actor_type/actor_id 컬럼만. 별도 actor view 는 추후.
- **별도 dashboard 페이지 없음**: 기존 dockview audit panel 을 확장 — 별도 route 추가하지 않음. M1 의 IDE-centric IA 와 일관.
- **export 5000 cap**: plan 명시 없음. JSONB 페치 + 직렬화 비용 vs 사용자 기대값 균형 — 큰 export 는 추후 streaming chunked 으로 무제한화.

### Cycle 4.7 — 비용 대시보드 + Prometheus exporter (✅ 완료 — *this commit*, OTel push 부분 deferred)

[plan §4.7](../../plan/m1/e4_integration_dogfood_geny.md#cycle-47-——-비용-대시보드--otel-prometheus-2-pr).

**스코프 축소 사유**: plan 의 OTel SDK init + Grafana dashboard JSON + compose Prometheus/Grafana profile 은 별도 dogfood (4.10) 와 함께. 본 cycle 은 **pull-based /metrics + cost dashboard endpoint + UI** 까지. opentelemetry-sdk 는 이미 deps 에 있어 운영자가 자체 OTLP collector 와 wire 가능 — push 측 wiring 은 deferred.

**서버 (4 module + 1 router + 2 test, 13 case):**
- `domains/cost/service.py` — `aggregate_summary(db, project_ids, since?, until?)` + `aggregate_daily_for_project(db, project_id, since?, until?)`. 둘 다 `agent_sessions` 테이블 직접 집계 (`cost_usd / input_tokens / output_tokens`). 일별 집계는 `created_at` 의 UTC date cast.
- `routers/cost.py` — 2 endpoint:
  - `GET /api/cost/summary?since&until` → 액터의 멤버십 프로젝트만 집계. `rows` + `total_*` 필드.
  - `GET /api/projects/{pid}/cost/daily?since&until` → 일별 row (sparse — 0인 날 미생성).
- `observability/metrics.py` — 자체 Counter / Gauge (no `prometheus_client` 의존, 모듈 메타클래스 회피). `MetricsRegistry` 가 dict 컨테이너. Gauge 는 `set_collector(async fn)` 로 scrape 시점 live refresh 지원.
- `observability/render.py` — Prometheus text exposition format 직접 렌더 (HELP/TYPE/value 라인 + label escape).
- `observability/instruments.py` — GAPT 메트릭 정의:
  - 카운터: `gapt_agent_cost_usd_total{project_id}`, `gapt_agent_input_tokens_total{project_id}`, `gapt_agent_output_tokens_total{project_id}`
  - 게이지: `gapt_sessions_active` (DB collector), `gapt_sandbox_count{state}` (DB collector)
  - `register_default_metrics(container)` 가 startup 시 collector wire — 컨테이너별 fresh registry (테스트 cross-pollution 방지).
- `routers/metrics.py` — `GET /metrics` (Prometheus pull). `refresh_collectors()` 호출 후 render.
- `container.py` — `AppContainer.registry: MetricsRegistry` 필드 추가. `app.py` 가 lifespan 아닌 `create_app` 시점에 `register_default_metrics` 호출 (테스트 lifespan 부재 케이스 커버).
- `routers/sessions.py` — `create_session` 의 cost callback 이 SSE publish 뿐 아니라 (a) 델타 기반 Prometheus counter 증분 (b) `agent_sessions` row 의 cost_usd / input_tokens / output_tokens 누적 update. fresh session 사용 — 외부 요청 트랜잭션과 deadlock 방지.

**테스트 (13 case):**
- `tests/observability/test_metrics.py` (7): counter 음수 거부, label 별 누적, gauge set, gauge collector refresh on render, label quote escape, empty registry → 빈 출력, reset idempotent
- `tests/cost/test_routes.py` (6, Postgres): summary 프로젝트별 집계, 비멤버 프로젝트 제외, since/until 윈도우, daily 일별 버킷 + 같은 날 합산, daily 403 (비멤버), /metrics 텍스트 형식 + `gapt_sessions_active`

**Gate:** server ruff/mypy clean (86 src), 330 server tests (+13). Web typecheck/lint/format clean, 86 web tests (+5), build 성공 (PWA precache 782 KiB).

**웹 (4 file + 1 route + 1 panel + 1 test, 5 case, i18n 19 키):**
- `src/api/cost.ts` — `getCostSummary({since,until})` + `getProjectCostDaily(pid, {since,until})` + 타입.
- `src/cost/CostPanel.tsx` — 비용 대시보드 패널:
  - 범위 preset (7일/30일/90일/전체)
  - 총합 dl (cost / tokens in / tokens out)
  - 프로젝트별 테이블 (display_name + slug + cost + tokens + sessions)
  - 프로젝트 row 클릭 → 일별 breakdown section + CSS 바 (max-day 대비 width %, no recharts)
- `src/routes/Cost.tsx` + `/cost` route (RequireAuth + AppShell)
- `src/ide/panels.tsx` 에 `CostPanelDock`, `DockviewShell.tsx` components 에 `cost: CostPanelDock` 등록 (워크스페이스 안에서도 패널 드래그 가능)
- i18n 19 키 (en+ko): cost.dashboard.title / loading / empty / refresh / range.{label,7d,30d,90d,all} / totals.* (3) / col.* (5) / daily.{title,empty,error}
- `tests/CostPanel.test.tsx` (5 case): 총합 + 테이블 렌더, 범위 변경 시 재페치, 프로젝트 클릭 → daily fetch, empty-state, API 에러 surface

**🧪 사용자가 직접 테스트할 수 있는 부분 — *이제 가능*:**

```bash
# 1. 서버 부팅 + 매직 링크 로그인 + 프로젝트 생성 (이전 cycle 흐름)
# 2. 채팅 세션 한 두번 돌려서 cost 누적 (agent 호출이 input/output tokens 채움)

# 3. 비용 대시보드 endpoint 직접
curl -b /tmp/cookies.txt "http://localhost:8001/api/cost/summary?since=2026-05-01T00:00:00Z" | jq
# {"rows":[...], "total_cost_usd":0.123, ...}

curl -b /tmp/cookies.txt "http://localhost:8001/api/projects/{pid}/cost/daily" | jq
# [{"date":"2026-05-20","cost_usd":0.05,...}, ...]

# 4. Prometheus scrape
curl http://localhost:8001/metrics
# # TYPE gapt_sessions_active gauge
# gapt_sessions_active 0
# # TYPE gapt_agent_cost_usd_total counter
# gapt_agent_cost_usd_total{project_id="01K..."} 0.123
# ...

# 5. 웹 UI: /cost 라우트 → 범위 select 변경 → 프로젝트 row 클릭하면 일별 CSS 바 펼침
```

#### Plan 카드 대비 변경

- **OTel SDK init + OTLP push 미구현**: plan 의 `gen_ai.*` semantic convention export. 이미 `opentelemetry-api/sdk/instrumentation-fastapi` 가 deps 에 있어 운영자가 자체 collector wire 가능. 자동 init 은 4.10 dogfood 단계.
- **Grafana dashboard JSON 미동봉**: plan 의 `compose/grafana/dashboards/gapt-overview.json`. compose Prometheus/Grafana profile 정비와 함께 4.10 dogfood 에서.
- **compose Prometheus/Grafana 미추가**: 4.10 dogfood 가 compose 전반 정비할 때 같이.
- **CAP 게이지 + UI 미구현**: plan 의 "cap 설정 + 게이지". cap 설정 자체가 SettingsService UI 가 필요 — 4.8 알림 cycle 의 cost-cap-80%-도달 트리거와 함께.
- **recharts 미사용**: plan 명시. 기존 메모리 (no decorative chrome / bundle size 우려) + Plan 3.10 deferred 카탈로그 → CSS 바로 대체.
- **prometheus_client 의존성 추가 안 함**: 자체 14줄짜리 exposition format 렌더가 충분. global registry / process collector 가 가져오는 부작용 회피.
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
- [x] Audit dashboard (4.6)
- [x] OTel + Prometheus exporter (4.7 — pull only; OTLP push deferred)
- [ ] 🎯 Dogfood: GAPT 가 GAPT 유지보수 (4.10)
- [ ] 🎯 Geny 첫 어댑트: 외부 IDE 0회 (4.11)

## 사용자 검증 게이트

각 cycle 의 ship 마다 *사용자가 직접 테스트 가능한 surface* 가 생기면 progress 카드에 명시. M1-E4 의 핵심 검증은 **4.10 dogfood + 4.11 geny adapt** — 둘 다 사용자가 운영 환경에서 직접 실행.

## Drift (cycle 종료 시 누적 기록)

*(아직 종료되지 않음)*
