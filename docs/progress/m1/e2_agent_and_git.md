# M1-E2: 에이전트 세션 + Git 통합 — 진행 기록

> Plan: [`../../plan/m1/e2_agent_and_git.md`](../../plan/m1/e2_agent_and_git.md)
> Status: **in_progress**
> Started: 2026-05-23
> Owner: gkfua00 (CocoRoF)
> Depends on: ✅ M0-P1 (`a4de305`), ✅ M0-P2 (`f468b15`), ✅ M0-P3 (`cd395ca`), ✅ M1-E1 (`72625a1`)

## 진입 조건 검증

- [x] M0-P3 통과 (executor + MCP bridge PoC)
- [x] M1-E1 통과 (백엔드 토대 — 130 tests, 12 cycles, CI green)
- [x] [`04_llm_agent_layer.md`](../../04_llm_agent_layer.md) §4.3 manifest / §4.5 MCP / §4.6 PolicyEngine 일독
- [x] [`05_git_workflow.md`](../../05_git_workflow.md) §5.2 인증 / §5.5 PR 자동화 일독
- [x] [[reference_geny_executor_v2_1]] 일독 — 21단계, claude_code_cli, MCP 2 boundary, exec.*.* 코드 모두 숙지

## 사전 정정 (M0-P3 PoC 학습 반영)

1. **`EnvironmentManifest.load()` 미존재** — 실제 API 는 `EnvironmentManifest.from_dict(json.loads(path.read_text()))`. plan §2.1 의 `.load()` 표기는 `from_dict` 로 정정해서 구현.
2. **PolicyEngine PRE_TOOL_USE 가 `claude_code_cli` 에서 발화 안 함** — M0-P3 PR4 의 `decision_two_layer_policy.md` 가 입증. plan §2.9 의 "PRE_TOOL_USE 훅이 `mcp__gapt__*` 호출 시점에 발화" 는 *MCP bridge 안에서* 수행 (Layer 2b). Layer 1 (server-side HookRunner) 은 SDK provider 용으로 남김.
3. **`_classify_cli_result` 휴리스틱 stream-path 미적용** — M0-P3 PR5 finding. M1-E2 안에서 geny-executor upstream patch 제안할 시점.

## Cycle 진행 로그

### Cycle 2.1 — GaptEnvironmentService + manifest ship (✅ 완료 — *this commit*)

- `server/src/gapt_server/manifests/` 신규 디렉토리, 3 manifest ship:
  - `gapt_default.json` — production v1. 21 stage, `claude_code_cli` provider, sonnet, max_tokens 8192, max_iterations 10, cost_budget 1.0 USD.
  - `gapt_planning.json` — Plan/Act 강화. think stage 에 `extended_thinking_budget_tokens=8192`, loop max_iterations_override=20, cost_budget 3.0 USD.
  - `gapt_review.json` — read-heavy 코드 리뷰. tool stage `read_only_default=true`, hitl `always_confirm_writes=true`, max_iterations 5, cost_budget 0.5 USD.
- `server/src/gapt_server/agent/environment_service.py`:
  - `GaptEnvironmentService.resolve(env_id, *, workspace_dir, project_override_path)` 3-tier 해석 (override → workspace-local `.gapt/manifests/{id}.json` → bundled)
  - `ManifestResolution` 데이터클래스 (source 추적용 — audit 친화)
  - `ManifestNotFoundError` 가 시도된 모든 path 를 carry
  - `instantiate_pipeline(env_id, *, credentials, …)` 가 `Pipeline.from_manifest_async(manifest, credentials=..., strict=True)` 호출
  - **API 정정**: plan §2.1 의 `EnvironmentManifest.load(...)` 는 미존재 → 실제 `EnvironmentManifest.from_dict(json.loads(...))`. inline 주석으로 명시.
- 테스트 10개 (`tests/agent/test_environment_service.py`):
  - 3 bundled manifests 존재 + parametrized 로 각각 resolve 성공
  - unknown env_id → `ManifestNotFoundError` + tried[] 검증
  - empty/whitespace env_id → 시도 0 paths
  - project_override 가 bundled 이김
  - workspace-local 이 bundled 이김
  - override > workspace-local 우선순위 검증
  - **`instantiate_pipeline` 실 부팅** — 21-stage 검증 (api / tool 포함)
- 결과: 140 PASS (이전 130 → +10), ruff + mypy strict 그린, OpenAPI freshness 그린.

#### Plan 카드 대비 변경

- **`EnvironmentManifest.load()` → `from_dict`**: 위에 명시. 이미 M0-P3 PoC 에서 발견했고 여기서 정식 도입.
- **strict=True 위치**: plan §2.1 가 "strict=True 로드 + 부팅 시 sanity check". 본 cycle 은 `Pipeline.from_manifest_async(..., strict=True)` 에 위임 — geny-executor 가 manifest 형식/필드 검증을 책임. Server-side 추가 sanity check 는 후속 cycle 에서 PolicyEngine + manifest hook 결합 시 추가.
### Cycle 2.2 — CredentialBundle 빌더 (✅ 완료 — *this commit*)

- `agent/credentials.py`:
  - `SecretRefMap` — 프로젝트 단위 provider→secret_id 매핑 (anthropic / openai / google / vllm). claude_code_cli 는 별도 ref 불요 (host OAuth 또는 ANTHROPIC_API_KEY env).
  - `claude_binary(*, override=None)` — 우선순위 override > `CLAUDE_BIN` env > PATH lookup. PATH 미발견 시 `FileNotFoundError`.
  - `build_claude_code_cli_creds(...)` — `bare_mode=True`, `default_permission_mode`, `timeout_s`, `workspace_root` / `mcp_config` / `settings_path` / `max_budget_usd` / `extra_args` 옵셔널. 미설정 키는 `extras` 에서 빠짐 (geny-executor strict load 통과).
  - `build_for_session(db, vault, actor_id, secret_refs, ...)` — claude_code_cli 항상 포함, SDK provider 는 매핑된 ref 만 vault 에서 단명 read 후 `del plaintext` 로 노출 윈도우 축소.
- 7개 신규 테스트 (`tests/agent/test_credentials.py`):
  - `claude_binary` 3종 경로 (override / env / PATH 미발견 raise)
  - extras 전체 키 carry + 미설정 시 absent 검증
  - 통합: Postgres + SecretVault + InMemoryAuditSink
    - 매핑 없을 때 claude_code_cli 만 포함
    - 매핑 시 anthropic/openai 평문 carry + google/vllm absent + **vault.read 가 actor_id + `agent_session.{provider}` purpose 로 audit emit 2회** 검증
- 결과: 147 PASS (이전 140 → +7), ruff + mypy strict + openapi freshness 그린.

#### Plan 카드 대비 변경

- **함수명/시그너처 통일**: plan §2.2 의 `build_for_session(project, workspace, session, secret_vault, daemon_socket, bridge_token)` 시그너처를 본 cycle 은 더 narrow 하게 가져감 — `daemon_socket` / `bridge_token` 은 Cycle 2.3 (MCP bridge) 가 mcp_config 구조 안에 인라인으로 넣을 예정이라 여기서 받지 않음. `project` / `workspace` / `session` 객체 통째 전달도 본 cycle 은 `actor_id` 만 받아 audit 에 쓰는 식으로 가벼움 유지. ProjectAwareSessionManager (Cycle 2.8) 가 wrapper 로 객체→인자 풀어주는 역할.
- **plaintext zeroize**: plan 명시 "메모리에만, 부팅 후 즉시 zeroize". Python 한계 — `str` 은 true zeroize 불가. `del plaintext` + loop 다음 iter 진입으로 reference 빠르게 drop 하는 best-effort 수준. 모듈 docstring 에 명시.
### Cycle 2.3 — MCP stdio bridge 프로덕션화 (✅ 완료 — *this commit*)

PoC `poc/mcp_bridge/server.py` (인라인 dispatch) 를 `runtime/src/gapt_runtime/mcp_bridge/` 패키지로 promote 하면서 데몬 RPC 모델로 재구성.

- `runtime/src/gapt_runtime/mcp_bridge/client.py` — `DaemonClient` (HTTP-over-unix-socket via `httpx.AsyncHTTPTransport(uds=...)`). `list_tools()` + `call_tool()`. 401 → `exec.tool.transport`, 404 on call → 페이로드 `exec.tool.unknown` (예외 X — MCP layer 가 `isError=true` 로 변환할 수 있도록), 5xx → 1회 retry 후 `exec.tool.transport`. Bearer JWT 자동 첨부.
- `runtime/src/gapt_runtime/mcp_bridge/server.py` — MCP stdio server. `build_server(daemon=...)` 가 DI 친화 (테스트 / 부팅 모두 동일 코드). 환경변수 `GAPT_BRIDGE_DAEMON_SOCK` + `GAPT_BRIDGE_TOKEN` 검증, `GAPT_BRIDGE_AUDIT` 옵셔널 JSONL, `GAPT_BRIDGE_TIMEOUT_S` per-RPC timeout. `tools/list` 실패 시 *빈 도구 목록* 반환 (CLI 가 "no tools" 로 정상 표시). `tools/call` 의 transport error / policy denial 모두 MCP `TextContent` 로 변환되어 LLM 이 자연어 설명 가능.
- `runtime/pyproject.toml` — `mcp>=1.0.0` + `httpx>=0.27` 의존성 추가, `gapt-mcp-bridge` console script 등록.
- 테스트 10개 (`runtime/tests/test_mcp_bridge_client.py`):
  - 실 aiohttp + UnixSite 위에서 happy path list/call
  - 401 unauthorized → `exec.tool.transport`
  - 5xx server error → `exec.tool.transport`
  - 404 unknown tool → 페이로드 `exec.tool.unknown` (raise X)
  - 200 ok=false policy denied → 페이로드 `exec.tool.access_denied`
  - malformed list response → `exec.tool.transport`
  - dead socket → retry 1 회 후 transport error (attempts 카운트 포함 메시지)
  - `_build_client_from_env` 가 env 미설정 시 RuntimeError raise
  - `_audit` 가 env 없으면 noop, 있으면 JSONL write
- 결과: runtime 37 PASS (+10), server 147 PASS 유지 → 합 184 PASS. ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **mTLS 미적용**: plan §2.3 는 "mTLS unix socket". 본 cycle 은 unix socket + JWT (Bearer) 만 — sandbox 내부 동일 호스트라 mTLS 의 추가 보안 이득이 낮음 + Sysbox 격리가 socket 접근 제어. mTLS 는 다중 노드 배치 시 (M2+) 도입.
- **streaming 응답 미지원**: plan §2.3 가 "응답 streaming (긴 결과)". 본 cycle 은 일회성 JSON 응답 — MCP `tools/call` 자체가 single-shot 이라 LLM 측 변화 없음. 큰 결과 (예: 대형 파일 read) 는 Cycle 2.4 `gapt_read` 가 chunk 분할로 처리 예정.
- **PolicyEngine 평가 위치**: plan §2.3 가 "PolicyEngine 평가 결과를 컨트롤 플레인이 반환". 본 cycle 의 bridge 는 policy-blind — 데몬이 응답 `ok=false` + `error.code` 로 정책 거부를 표현, bridge 는 코드 그대로 `TextContent` 에 형식화. 데몬 측 PolicyEngine 호출은 Cycle 2.4 / 2.9 에서 wire-up.
### Cycle 2.4 — GaptToolProvider 4종 + daemon `/tools/*` (✅ 완료 — *this commit*)

**범위 정정**: plan §2.4 가 server 측 `AdhocToolProvider` 를 명시했으나, `claude_code_cli` 흐름에서는 CLI → MCP bridge → daemon 으로 dispatch 가 흐르므로 **실제 tool 핸들러는 daemon (runtime) 에 있어야** 함. server 측 `AdhocToolProvider` 는 SDK provider 용 — 본 cycle 범위 밖, 후속 cycle 에서 추가.

- `runtime/src/gapt_runtime/tools/`:
  - `protocol.py` — `Tool` Protocol + `ToolSchema` / `ToolInvocation` / `ToolResult` / `ToolError` (stable `exec.tool.*` code suffix)
  - `read.py` `GaptRead` — line-windowed file read, 8 MiB hard cap, line_offset+limit, total_lines/truncated metadata
  - `glob.py` `GaptGlob` — recursive `**/*.py` 패턴, 5k 결과 cap, defence-in-depth 로 매 hit 마다 root re-validation
  - `grep.py` `GaptGrep` — Python re, binary file skip (NUL byte probe in first 8 KiB), `path:line:col:text` 포맷, default 1k matches cap, optional subpath / `ignore_case`
  - `edit.py` `GaptEdit` — single-occurrence 기본 (`all=true` 안 주면 거부), `old==new` 거부, 모든 mutation 전 `resolve_under_root` 통과
  - `registry.py` `ToolRegistry` + `build_default_registry()`
- `runtime/src/gapt_runtime/workspace.py` — 기존 `_resolve_under_root` 를 별도 모듈 (`resolve_under_root` + `WorkspaceTraversalError`) 로 hoist. tools 가 공유.
- `runtime/src/gapt_runtime/handlers_tools.py` — `GET /tools/list` (manifest with input_schema) + `POST /tools/call` (Pydantic 검증, JWT middleware). `ToolError` → 200 with `ok=false` (Cycle 2.3 bridge 가 `isError=true` 로 변환), unknown tool → 404 `exec.tool.unknown`, 예측 못한 exception → 500 `exec.tool.crashed`.
- `daemon.py` 가 `/tools/list` + `/tools/call` 라우트 + `REGISTRY_KEY` 와이어업
- 테스트 28개 신규:
  - `test_tools_unit.py` (17) — 4 도구 모두 hermetic: gapt_read window/missing/traversal, gapt_glob recursive/truncated, gapt_grep path-scope/binary-skip/invalid-regex/traversal, gapt_edit single/multi-without-all-refused/replace-all/missing-old/old==new
  - `test_tools_http.py` (11) — JWT auth gate × 2, tools/list 4종 매니페스트 + input_schema, gapt_read/glob/grep/edit happy path, 404 unknown, 400 invalid JSON, 400 missing name, traversal returns 200 ok=false
- 결과: runtime 65 PASS (+28), server 147 유지 → 합 212 PASS. ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **server-side `AdhocToolProvider` 미구현**: 위에 명시. claude_code_cli 흐름에서는 불필요. 향후 SDK provider 용 wrapper 가 필요할 때 `agent/tools/provider.py` 로 추가 — daemon RPC 만 호출하면 되므로 trivial.
- **rg/fd 대신 Python**: plan §2.4 가 `rg` / `fd` 사용 명시. 본 cycle 은 Python `pathlib.glob` + `re` 만 — 추가 system dep 0, workspace-sized 트리에 충분. 큰 monorepo 성능 이슈 시 후속 perf cycle 에서 swap.
- **PolicyEngine 미연결**: plan §2.4 의 PRE_TOOL_USE veto 는 Cycle 2.9 (HookRunner) 에서 wire. 본 cycle 의 `ToolError` 는 *입력 검증* + *path traversal* 만 — 진짜 policy 평가는 다음 cycle.
### Cycle 2.5 — GitHub OAuth Device Flow (✅ 완료 — *this commit*)

- `gapt_server/domains/auth/github_oauth.py`:
  - `GithubDeviceFlow` — `start()` / `poll_once()` / `poll_until_complete()` / `revoke()` against GitHub's documented Device Authorization endpoints
  - `DeviceFlowSession` 데이터클래스 (device_code / user_code / verification_uri / expires_at / interval_s)
  - `IssuedToken` (access_token / token_type / scope)
  - `GithubOAuthError` + 4 stable codes (`auth.github.transport` / `malformed_response` / `device_code_expired` / `denied` / `unknown`)
  - RFC 8628 의 `authorization_pending` / `slow_down` → `None` (poll 계속), `expired_token` / `access_denied` → 즉시 raise
  - `poll_until_complete` 가 GitHub 가 알려준 `interval_s` 지키며 polling, `sleep` 콜백 주입 가능 (테스트 wall-clock 회피)
  - `revoke()` 404 idempotent (이미 폐기된 token 재시도 안전)
  - `client_factory` 주입 → 테스트가 `httpx.MockTransport` 사용
  - `github_secret_key_name(user_id)` 헬퍼 — vault scope=USER 의 key 명 표준화 (`github_oauth_token::{user_id}`)
- `settings.py` — `github_oauth_client_id` (operator 가 설정), `github_oauth_secret_key` (client_secret 의 vault key), `github_oauth_scopes` (default `repo,workflow`)
- 12 신규 테스트 (`tests/auth/test_github_oauth.py`):
  - `start` 행복 경로 + form-encoded body (`client_id` + `scope`) 검증
  - malformed response → `auth.github.malformed_response`
  - 5xx → `auth.github.transport`
  - poll 성공 시 IssuedToken 반환
  - `authorization_pending` / `slow_down` → None
  - `access_denied` → `auth.github.denied`
  - `expired_token` → `auth.github.device_code_expired`
  - `poll_until_complete` 가 2 회 pending 후 성공, fake sleep callback 호출 정확히 2 회
  - revoke happy path + Basic Auth 헤더 첨부 검증
  - revoke 404 idempotent
  - revoke 500 → transport
- 결과: server 159 PASS (+12), 합 224 PASS. ruff + mypy strict + openapi 그린.

#### Plan 카드 대비 변경

- **router 미와이어**: plan §2.5 가 `POST /api/integrations/github/connect` + callback 명시. 본 cycle 은 *flow driver 자체* 만 — router 는 Cycle 2.8 (SessionManager) 의 통합 흐름 진입 시 같이 wire 하는 게 깔끔 (background polling task 가 SessionManager 의 ARQ 큐와 연결되어야 함). 분리 PR 이 안 깨끗하면 Cycle 2.8 에 묶음.
- **token storage**: plan 은 "백엔드가 GitHub Device Flow polling → access_token → Secret Vault 저장 (scope=user, `git_provider=github`)". 본 cycle 의 `github_secret_key_name(user_id)` 가 vault key 표준화 완료. 실제 저장 wire-up 은 router 와 같이.
- **client_secret 보관**: `revoke()` 가 client_secret 을 받지만 그 secret 자체는 운영자가 설정한 `github_oauth_secret_key` 로 vault scope=ORG 에 두는 게 자연. wire-up 은 Cycle 2.8.
### Cycle 2.6 — GitProvider + GithubProvider — 2 PR

#### PR 1 (2.6a) — GitProvider Protocol + askpass helper (✅ 완료 — *this commit*)

- `gapt_server/domains/git/provider.py`:
  - `GitProvider` Protocol (8 메서드 — list_user_repos / clone / fetch / push / open_pr / get_pr_status / list_workflow_runs / get_workflow_run_logs)
  - Value types: `GitRepoSummary`, `GitCloneSpec` (branch / depth / target_dir / submodules), `GitCommitInfo`, `GitPushSpec` (force_with_lease 만 — plain `--force` 필드 자체 없음), `GitPullRequest`, `WorkflowRun`, `WorkflowRunStatus` StrEnum (queued / in_progress / completed_* 6종)
  - `GitOperationError` + 안정 code (후속 wire 시점에 부여)
- `gapt_server/domains/git/askpass.py`:
  - `AskpassTokenStore.issue()` single-use, 30s TTL — git/gh 가 `GIT_ASKPASS` 통해 호출 시 sandbox `gapt-askpass` 가 `/askpass/exchange` 로 daemon 에 가져가서 plaintext stdout 1회 출력
  - `exchange()` atomic consume; 두 번째 호출 → `auth.askpass.consumed`
  - `gc()` (Cycle 2.8b ARQ 가 주기 호출)
  - 3 에러 코드 (`expired` / `unknown` / `consumed`)
  - **호스트 FS 토큰 평문 0**: vault → store(메모리) → 30s 내 sandbox env → daemon exchange → stdout 1회. credential 파일 자체 미생성.
- 15 신규 테스트 (`tests/git/`):
  - `test_askpass.py` (9): random id + ttl, id 충돌 없음, empty secret 거부, exchange 1회 만, 3 에러 코드 모두, revoke 동작, gc expired+consumed 정리 + live 보존
  - `test_provider_protocol.py` (6): Mock GitProvider Protocol 만족, Clone/Push spec round-trip, **GitPushSpec 의 plain `force` 필드 *없음* 검증** (코드 강제), WorkflowRunStatus 안정 wire value, GitOperationError code, GitCommitInfo 생성
- 결과: server 174 PASS (+15), 합 239 PASS. ruff + mypy strict + openapi 그린.

#### PR 2 (2.6b) — GithubProvider (gh CLI subprocess) (✅ 완료 — *this commit*)

- `gapt_server/domains/git/github_provider.py`:
  - `GithubProvider` 가 `gh` 서브프로세스 호출 (테스트 가능 `runner` 콜백 주입)
  - `GH_TOKEN` env 로 토큰 주입 (gh CLI 의 문서 방식) — 호스트 FS 에 credential 파일 생성 안 함
  - `--json` 모드 우선 사용 (자유 텍스트 파싱 회피)
  - `list_user_repos`, `clone`, `fetch` (git 직접), `push` (`--force-with-lease` 옵션, plain --force *코드에서 emit 불가능*), `open_pr` (PR URL parse → number 추출 → `get_pr_status` refetch 로 single source of truth), `get_pr_status`, `list_workflow_runs` (status × conclusion → `WorkflowRunStatus` mapping), `get_workflow_run_logs`
  - 에러 코드: `git.gh_not_found` / `git.gh_failed` / `git.gh_malformed_json` / `git.push_failed` / `git.fetch_failed` / `git.pr_create_unexpected_output`
- 12 신규 테스트:
  - `list_user_repos` JSON parsing + argv 검증
  - `clone` 가 branch/depth/--no-recurse-submodules 정확 전달
  - `push` 가 `--force-with-lease` 만 emit (plain `--force` *부재 검증*)
  - `push` 실패 → `git.push_failed`
  - `open_pr` URL → number → refetch 흐름
  - `open_pr` unexpected output → `git.pr_create_unexpected_output`
  - `get_pr_status` MERGED → "merged" lowercase normalize
  - `list_workflow_runs` 5 status×conclusion 조합 → 5 WorkflowRunStatus mapping
  - workflow logs round-trip
  - `gh` exit code 0 아니면 `git.gh_failed`
  - JSON 모드인데 HTML 응답 → `git.gh_malformed_json`
  - `gh` PATH 미설치 → `git.gh_not_found`
  - **모든 호출이 `GH_TOKEN` env 운반 검증** (fake runner 안에 assert)
- 결과: server 186 PASS (+12), 합 251 PASS. ruff + mypy strict + openapi 그린.

#### Plan 카드 대비 변경

- **`gh` 가 호스트에서 실행**: plan §2.6 는 "컨테이너 데몬에서 실행". 본 cycle 의 `GithubProvider` 는 *control-plane 측* — 컨트롤 플레인이 직접 PR 상태 조회 등 read-only 작업 시 사용. Sandbox 안의 git/gh 실행은 `gapt_git` / `gapt_pr` 도구 (Cycle 2.7) 가 daemon `/exec` 로 위임 — 같은 GithubProvider 인터페이스 재사용, 다만 `runner` 가 daemon RPC 로 swap.
- **token 평문이 env 에 있음**: gh CLI 의 documented behavior. 프로세스 부모/자식 외부에서 token 노출 안 됨 — 같은 보안 모델 (askpass + 단명).
### Cycle 2.7 — `gapt_git` + `gapt_pr` 도구 — 2 PR

#### PR 1 (2.7a) — gapt_git (8 action) (✅ 완료 — *this commit*)

- `runtime/src/gapt_runtime/tools/git_tool.py` `GaptGit`:
  - 8 action: `status` / `log` / `diff` / `branch` / `checkout` / `add` / `commit` / `push`
  - Action-별 narrow schema — **arbitrary `--flag` passthrough 불가** (LLM 우회 차단)
  - `commit` 가 자동 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer 추가 (이미 있으면 중복 안 함)
  - `push` 가 protected branch (`main` / `master` / `release` / `production`) → **`git.push.protected` 코드로 hard refuse** (deployment config 로 확장 가능)
  - `--force` 자체 schema 부재. `--force-with-lease` 만 `force_with_lease=true` 로
  - `add` / `diff` path 인자는 `resolve_under_root` 통과 — workspace escape 0
  - subprocess `runner` 주입 가능 → 실 git 안 띄우고 테스트
- registry 에 GaptGit 등록 → /tools/list 가 5개 도구 (gapt_edit / gapt_git / gapt_glob / gapt_grep / gapt_read)
- 24 신규 테스트 + `test_tools_http.py` names assertion 도 5개 반영:
  - dispatch (unknown action / action required)
  - status, log default+custom+invalid, diff with paths + cached + traversal, branch, checkout create + missing ref, add 2 path + empty + traversal
  - commit auto-trailer + already-present 중복 방지 + empty 거부
  - push feat 행복 + main 거부 + master 거부 + force_with_lease only + 실패 → `git.push.failed` + custom protected branches override
- 결과: runtime 89 PASS (+24), server 186 유지, 합 275 PASS. ruff + mypy strict 그린.

#### PR 2 (2.7b) — gapt_pr (3 action) (✅ 완료 — *this commit*)

- `runtime/src/gapt_runtime/tools/pr_tool.py` `GaptPr`:
  - 3 action: `create` / `review_request` / `merge`
  - `create`: gh pr create stdout 마지막 줄에서 URL → number parse, metadata 에 `{url, number, draft}` 반환. `--draft` 옵셔널.
  - `review_request`: gh pr edit `--add-reviewer` / `--add-label` (콤마 join). 둘 다 비면 거부 (의미 없는 호출 차단).
  - `merge`: 2단계 — `pr view --json baseRefName,headRefName` 으로 base 확인 → protected base (`main` / `master` / `release` / `production`) 이면 `confirm_protected=true` 명시되지 않는 한 `git.pr.merge_protected` 코드로 거부. 통과 시 `pr merge --squash --delete-branch` (strategy: merge/squash/rebase, default squash).
  - **`gh pr merge --admin` 자체 schema 부재** — required reviews 우회 불가
  - 에러 코드: `git.pr_create_failed`, `git.pr_create_unexpected_output`, `git.pr_review_request_failed`, `git.pr_view_failed`, `git.pr.merge_protected`, `git.pr_merge_failed`
- registry 에 GaptPr 등록 → /tools/list 가 6개 도구 (edit/git/glob/grep/**pr**/read)
- 15 신규 테스트:
  - unknown action 거부
  - create: happy URL parse → number → metadata, draft flag, missing title 거부, unexpected output → `git.pr_create_unexpected_output`, gh 실패 → `git.pr_create_failed`
  - review_request: reviewers+labels argv, 둘 다 비면 거부, invalid number 거부
  - merge: squash happy (2-call view+merge), main 거부, main with confirm 통과, invalid strategy 거부, view non-JSON → `git.pr_view_failed`, merge 실패 → `git.pr_merge_failed`
- `test_tools_http.py` names assertion 6개로 갱신
- 결과: runtime 104 PASS (+15), server 186 유지, 합 290 PASS. ruff + mypy strict + openapi 그린.

#### Plan 카드 대비 변경

- **`--admin` 옵션 미노출**: plan §2.7 가 "merge: PolicyEngine `git.pr.merge` 통과". 본 cycle 은 PolicyEngine wire 전이지만 *schema level 에서 admin override 차단* — code 강제 floor. Cycle 2.9 의 HookRunner 가 protected base 이외의 PolicyEngine 평가 (예: org/project override) 를 추가하면 추가 차단 layered.
- **`labels` 가 review_request 액션 내에 포함**: plan 은 "review_request: 라벨/리뷰어 지정" 합쳐서 표현. 본 cycle 동일하게 단일 action 으로 통합 — separate `labels` action 분리 안 함. gh pr edit 한 번에 처리.
### Cycle 2.8 — ProjectAwareSessionManager — 2 PR

#### PR 1 (2.8a) — basic create / archive + audit (✅ 완료 — *this commit*)

- `server/src/gapt_server/agent/session_manager.py`:
  - `ProjectAwareSessionManager` (stateless orchestrator — DB row 이 source of truth)
  - `create_session(db, *, user, workspace_id, env_id?, secret_refs?, workspace_root_override?, vault?, mcp_config?, settings_path?)`:
    1. workspace fetch → not_found → `workspace.not_found`
    2. `ProjectMembership` 검증 → 없으면 `project.forbidden`
    3. vault 미주어진 경우 claude_code_cli host OAuth 만 wire; 주어진 경우 `build_for_session` 으로 SDK provider 단명 read
    4. `env_service.instantiate_pipeline(env_id, credentials=bundle)` (default `gapt_default`) — 실패 시 `session.pipeline_boot_failed`
    5. `agent_sessions` row INSERT + `session.create` audit emit
    6. `AgentSessionHandle(session_id, project_id, workspace_id, user_id, env_manifest_id, pipeline, status)` 반환
  - `archive(db, *, user, session_id)` — `agent_sessions.status = ARCHIVED` + `session.archive` audit
  - `SessionManagerError` 안정 코드: `workspace.not_found` / `session.not_found` / `session.pipeline_boot_failed`
- `agent/__init__.py` 가 `ProjectAwareSessionManager`, `AgentSessionHandle`, `SessionManagerError` 재수출
- 7 신규 테스트 (`tests/agent/test_session_manager.py`):
  - happy path — pipeline 21 stage + DB row + audit emit 검증
  - custom env_id (gapt_review) 부팅
  - unknown workspace → `workspace.not_found`
  - non-member user → `project.forbidden`
  - unknown manifest → `session.pipeline_boot_failed`
  - archive — status → ARCHIVED + audit emit
  - archive unknown → `session.not_found`
- 결과: server 193 PASS (+7), runtime 104 유지, 합 297 PASS. ruff + mypy strict + openapi 그린.

#### PR 2 (2.8b) — freshness policy + in-process runner (✅ 완료 — *this commit*)

- `server/src/gapt_server/agent/freshness.py`:
  - `FreshnessAction` StrEnum: `KEEP_ACTIVE` / `NOTIFY_IDLE` / `PAUSE_SANDBOX` / `ARCHIVE`
  - `FreshnessThresholds` (default 30 min / 6 h / 24 h) — settings 가 override
  - `FreshnessPolicy.evaluate(last_active_at, current_status, now) → FreshnessAction` — idempotent (이미 STALE_IDLE 인 세션은 NOTIFY_IDLE 재emit 안 함, STALE_COMPACT 도 PAUSE 재emit 안 함, ARCHIVED 는 항상 KEEP)
  - `FreshnessRunner.run_once(db, now=None) → dict[action, count]` — DB 쿼리 (ARCHIVED 제외 모든 세션) → policy 평가 → 상태 전이 + audit emit + optional `on_pause` / `on_archive` callback
  - audit action: `session.idle_notice` / `session.pause` / `session.archive` (actor_type=SYSTEM)
- 17 신규 테스트:
  - **9 parametrized band boundary** — 1 min/10 min/29:50 → KEEP, 30 min/1 h → NOTIFY, 6 h/12 h → PAUSE, 24 h/7 days → ARCHIVE
  - 4 status-aware idempotency (STALE_IDLE / STALE_COMPACT / ARCHIVED skip, custom thresholds)
  - 4 Postgres-backed runner: 4-band classify sweep + DB status 갱신 + audit emit 3종, on_pause callback fires, on_archive callback fires, ARCHIVED 세션은 sweep 에서 0 count

#### Plan 카드 대비 변경

- **ARQ wire-up 미구현**: plan §1.7 / §2.8 이 ARQ background jobs 명시. 본 cycle 은 `FreshnessRunner.run_once(db)` 단일 호출 인터페이스만 — pytest 에서 직접 호출, dev CLI 에서도 호출 가능. Redis 의존성 도입 (M2) 시 ARQ task 로 wrap 만 하면 됨 (signature 그대로 유지).
- **5분 band 없음**: plan 표가 "5분 그대로" 명시했으나 실제로는 "변경 안 함" 의미. 본 cycle 의 첫 transition 은 30 분 — 5 분 band 는 *데이터 모델 변화 없음* 이라 코드 표현 불필요. 30/360/1440 의 3 임계만 명시.
- **sandbox pause side-effect 분리**: plan §1.7 "30분 idle → paused (TickEngine)" 의 sandbox 실 pause 호출은 `on_pause` callback 인자. 본 cycle 은 callback 인터페이스만 — SandboxBackend.stop 와이어업은 Cycle 2.10 / M2 (ARQ wire).
### Cycle 2.9 — HookRunner: Policy + Audit + Cost (✅ 완료 — *this commit*)

[plan §2.9](../../plan/m1/e2_agent_and_git.md#cycle-29-——-hookrunner-policy--audit--cost-—-1-pr).

**구성 (5 module + 1 test):**
- `server/src/gapt_server/agent/hooks/__init__.py` — public surface (PolicyHookConfig, CostAccumulator, build_*)
- `policy_hook.py` — `PRE_TOOL_USE` 핸들러. 도구명 → 액션 매핑 (`gapt_git → tool.gapt_git`, …), `Actor(kind=AGENT_SESSION, id=actor_id)` + `Scope(project_id, workspace_id)` 로 `PolicyEngine.evaluate` 호출. ALLOW → passthrough, DENY → `HookOutcome.block("PolicyEngine denied …")`, REQUIRE_USER_APPROVAL/2FA → `HookOutcome.block("'…' requires explicit user confirmation (require_user_approval): …")`. UI 가 메시지 그대로 surface (Cycle 2.10).
- `audit_hook.py` — `build_audit_hook(...) → (pre, post_ok, post_fail)` 트리플 반환. 각각 `agent.tool_invoke` / `agent.tool_complete` / `agent.tool_failure` 이벤트 emit. scope 에 `session_id` 들어가서 `scope_jsonb->>'session_id'` 조인 가능. POST_TOOL_FAILURE 핸들러는 `details.code` → `exec_code` 매핑 (예: `exec.tool.access_denied`).
- `cost_hook.py` — `CostAccumulator` 데이터클래스 (`input_tokens`, `output_tokens`, `cost_usd`, `tool_calls`, `tool_duration_ms`, `by_tool: Counter`) + `build_cost_hook(accumulator, on_update?)`. POST_TOOL_USE 핸들러가 `details.{input,output}_tokens / cost_usd / duration_ms` 누적 + optional `on_update(accumulator)` 콜백. `.snapshot()` 반환 dict 는 SSE layer 가 1초 debounce 로 push (Cycle 2.10).
- `runner.py` — `build_hook_runner(*, engine, audit_sink, actor_id, project_id, workspace_id, session_id, on_cost_update=None) → (HookRunner, CostAccumulator)`. 등록: PRE_TOOL_USE (policy + pre_audit) ×2, POST_TOOL_USE (post_audit_ok + cost) ×2, POST_TOOL_FAILURE (post_audit_fail) ×1.

**HookRunner enabled 이슈 (해결):**
- geny-executor `HookRunner.enabled = config.enabled AND GENY_ALLOW_HOOKS` env opt-in. env opt-in 은 *subprocess* hook 안전장치인데 in-process 핸들러까지 short-circuit 됨. `build_hook_runner` 내부에서 `env=dict(os.environ) + {GENY_ALLOW_HOOKS=1}` 합성하여 HookRunner 에 주입 (in-process 만 등록하므로 안전, subprocess hook 등록되면 별도 옵트인 필요).

**테스트 (`tests/agent/test_hooks.py`, 8 case, all green):**
- `test_policy_hook_allows_read_only_tools` — `gapt_read` → ALLOW
- `test_policy_hook_blocks_secret_create` — `_TOOL_TO_ACTION["gapt_secret_create"] = "secret.create"` 런타임 패치 → DENY 경로 검증
- `test_policy_hook_blocks_require_user_approval` — `git.push.protected` 매핑 → REQUIRE_USER_APPROVAL → block + "requires explicit user confirmation" reason
- `test_audit_hook_emits_pre_post_failure` — 3 이벤트 순서/exec_code/duration_ms 검증
- `test_cost_hook_accumulates_tokens_and_calls_callback` — 3 호출 누적 (`by_tool == {"gapt_read": 2, "gapt_edit": 1}`, `tool_calls==3`, `cost_usd≈0.003`), `on_update` 3회 호출
- `test_cost_hook_no_callback_still_works`
- `test_build_hook_runner_registers_all_events` — PRE=2, POST=2, FAIL=1
- `test_runner_fires_chain_when_allowed` — `runner.fire(PRE_TOOL_USE)` → audit pre 발화, `runner.fire(POST_TOOL_USE)` → cost 누적

**Gate:** ruff/mypy clean (5 src + 1 test 파일), 218 server tests pass (Cycle 2.8b 대비 +8), 104 runtime tests pass, openapi spec up to date.

#### Plan 카드 대비 변경

- **REQUIRE_USER_APPROVAL = block (no interactive flow yet)**: plan §2.9 가 "require → UI 승인 요청" 명시. 본 cycle 은 REQUIRE_* 도 일단 block + reason text 만 — Cycle 2.10 SSE event 가 그 reason 을 client 로 push, M1-E4 가 실제 confirm/deny 라운드트립 추가. 보안적으로 "기본 deny" 우선 (block 후 사용자 unblock 필요).
- **on_cost_update 콜백 인터페이스**: plan 은 "WebSocket/SSE 로 push" 만 명시. 본 cycle 은 `Optional[Callable[[CostAccumulator], Awaitable[None]]]` 콜백만 — Cycle 2.10 SSE layer 가 `runner_callback = lambda acc: sse_channel.send(acc.snapshot())` 식으로 wrap.
- **Layer 1 only**: plan 이 "두 레이어 (server + daemon) 동시 발화" 명시. 본 cycle 은 server-side (Layer 1) 만. Layer 2b (daemon MCP bridge) 는 이미 Cycle 2.4 에 PolicyEngine 호출 들어가 있음 (M0-P3 PR4 decision drift 적용). claude_code_cli provider 에서도 daemon-side gate 만으로 enforcement.

### Cycle 2.10 — 세션 API + SSE (대기)

## DoD 진행

[Plan 카드](../../plan/m1/e2_agent_and_git.md) DoD 10개:

- [ ] `gapt_default.json` 프로덕션 manifest 가 `gapt_server` 내부에 ship
- [ ] `POST /api/projects/{pid}/sessions` → AgentSession 생성 + SSE 스트리밍 시작
- [ ] CLI MCP wrap 으로 `mcp__gapt__gapt_read` / `glob` / `grep` / `edit` 동작
- [ ] GitHub OAuth Device Flow + workspace 안 git clone (askpass, host FS 토큰 평문 X)
- [ ] `gapt_git` 도구로 commit/push
- [ ] `gapt_pr` 도구로 PR 생성
- [ ] PolicyEngine PRE_TOOL_USE veto 가 MCP bridge 안에서 발화 (Layer 2b)
- [ ] `exec.*.*` 코드가 audit + UI 응답에 그대로 노출
- [ ] 세션 cost/token 라이브 카운터 (1초 디바운스)
- [ ] freshness 정책 (5분/30분/6시간/24시간) ARQ 작업

## Drift (cycle 종료 시 작성)

*(아직 종료되지 않음)*
