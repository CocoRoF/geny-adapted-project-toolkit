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
### Cycle 2.4 — GaptToolProvider (Read/Glob/Grep/Edit) (대기)
### Cycle 2.5 — GitHub OAuth Device Flow (대기)
### Cycle 2.6 — GitProvider + GithubProvider — 2 PR (대기)
### Cycle 2.7 — `gapt_git` + `gapt_pr` 도구 — 2 PR (대기)
### Cycle 2.8 — ProjectAwareSessionManager — 2 PR (대기)
### Cycle 2.9 — HookRunner: Policy + Audit + Cost (대기)
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
