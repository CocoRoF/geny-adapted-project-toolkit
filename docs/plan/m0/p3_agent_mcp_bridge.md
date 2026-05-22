# M0-P3: 에이전트 + MCP bridge PoC

> Status: planned
> Estimated: 5 작업일 / 6 PR
> Depends on: M0-P1, M0-P2
> Blocks: M1-E2 (에이전트 세션 + Git 통합)
> Relates to: [`../../04_llm_agent_layer.md`](../../04_llm_agent_layer.md) (전체), [`../../09_security_authz_observability.md`](../../09_security_authz_observability.md) §9.2.3

## 목적 (한 줄)
geny-executor 2.1.0의 `Pipeline.from_manifest_async`로 `claude_code_cli` provider 기반 세션을 부팅하고, **host MCP wrap**으로 호스트 도구를 CLI의 LLM에게 노출시키며, `HookRunner`의 `PRE_TOOL_USE` 훅에서 정책 거부가 실제로 *dispatch를 막는*지 확인한다.

## 진입 조건
- [ ] M0-P1, M0-P2 통과
- [ ] 사용자가 `claude` CLI 최신 버전 설치 (`runtime` 이미지에 포함 또는 호스트)
- [ ] Anthropic OAuth subscription (또는 API key)
- [ ] geny-executor 2.1.0+ PyPI에서 import 가능
- [ ] [[reference_geny_executor_v2_1]] 일독

## DoD (Definition of Done)
- [ ] `poc/executor_agent/` + `poc/mcp_bridge/` 산출물
- [ ] `gapt_default.json` 초안 manifest 1개로 `Pipeline.from_manifest_async()` 부팅 성공
- [ ] `claude_code_cli` provider로 "Hello, what's 2+2?" 응답
- [ ] **MCP stdio bridge** (`gapt.mcp_bridge.server`) ~150 LoC로 동작 — CLI가 `mcp__gapt__hello` 도구를 native MCP로 호출 → host registry로 라우팅 → 결과를 CLI에 반환
- [ ] **`HookRunner.PRE_TOOL_USE`** 더미 hook이 `tool_input` 검증 후 `ToolFailure(ACCESS_DENIED)` 발생 → CLI 안에서 도구 dispatch가 *명시적으로 거부*되며 audit 이벤트 기록
- [ ] `EventBus` 구독으로 `api.*` / `tool.call_start` / `tool.call_complete` 모두 캡처되어 JSONL 파일에 기록
- [ ] 비용/토큰 누계가 응답에 표시 (cost_usd, input_tokens, output_tokens)
- [ ] 에러 시나리오 4개 (`exec.cli.binary_not_found` / `exec.cli.auth_failed` / `exec.cli.timeout` / `exec.cli.permission_denied`) 재현 + audit에 `exec.*.*` 코드 그대로 기록

## 작업 항목 (세부)

### 1. PoC용 manifest 초안 (`gapt_default.json` v0)
- 위치: `poc/executor_agent/manifests/gapt_default.v0.json`
- 21 stages + `tools.built_in: ["Read"]` 최소만 + `tools.external: ["gapt_hello"]` + `tools.mcp_servers: []` (host-attached MCP는 PoC에선 X, CLI MCP wrap만 검증)
- `stages[6].config.provider = "claude_code_cli"`, `model: "sonnet"`, `max_tokens: 4096`
- `max_iterations: 5`, `cost_budget_usd: 0.10` (PoC 안전망)

### 2. CredentialBundle 빌더
- `poc/executor_agent/credentials.py`:
  ```python
  def build(claude_binary: str, bridge_socket: str, bridge_token: str) -> CredentialBundle: ...
  ```
- `claude_code_cli` provider용:
  - `api_key`: 환경변수 `ANTHROPIC_API_KEY` 또는 빈값(OAuth path)
  - `binary_path`: `/usr/local/bin/claude` (runtime 이미지) or 사용자 PATH
  - `extras`:
    - `bare_mode: True` (OAuth path 자동 strip)
    - `default_permission_mode: "default"`
    - `settings_path: '{"permissions":{"allow":["mcp__gapt","Read"]}}'`
    - `mcp_config: {"mcpServers": {"gapt": {...}}}`
    - `timeout_s: 60.0`
    - `max_budget_usd: 0.10`

### 3. MCP stdio bridge 구현 (~150 LoC, stdlib only)
- 위치: `poc/mcp_bridge/server.py`
- 참고: `docs/claude_code_cli.md` "Per-session MCP wrap" + Geny의 [`geny_mcp_bridge.py`](https://github.com/CocoRoF/Geny/blob/main/backend/scripts/geny_mcp_bridge.py)
- 흐름:
  1. stdin/stdout JSON-RPC 루프
  2. `initialize` → 기본 응답
  3. `tools/list` → 호스트 RPC로 도구 카탈로그 가져옴 → MCP 스펙 응답
  4. `tools/call` → 호스트 RPC로 dispatch (with PRE_TOOL_USE 평가) → 결과를 MCP `tool_result`로 반환
- 호스트와의 통신: unix socket(`/run/gapt/poc.sock`) HTTP + JWT (bridge 부팅 시 환경변수로 받음)
- 도구 dispatch 응답 코드 매핑:
  - `ToolFailure(ACCESS_DENIED)` → `{"isError": true, "content": [{"type": "text", "text": "Policy denied: ..."}]}`
- 환경변수:
  - `GAPT_BRIDGE_SOCKET`, `GAPT_BRIDGE_TOKEN`, `GAPT_SESSION_ID`

### 4. PoC 호스트 (FastAPI 또는 단순 asyncio)
- 위치: `poc/executor_agent/host.py`
- 단일 unix socket 서버:
  - `POST /tools/list` → `[{"name": "gapt_hello", "input_schema": {...}, "description": "..."}]`
  - `POST /tools/call` → `{"result": "..."}` 또는 `{"error": {"code": "...", "message": "..."}}`
- 도구 카탈로그:
  - `gapt_hello(name: str) -> str` — 단순 echo
  - `gapt_unsafe(cmd: str) -> str` — 무엇이든 *PRE_TOOL_USE 훅에서 거부* (deny 검증용)
- PRE_TOOL_USE 훅 (호스트 측, geny-executor HookRunner와 *별도*로 bridge가 호출):
  ```python
  if tool_name == "gapt_unsafe":
      raise ToolFailure("Policy denied (PoC)", code="exec.tool.access_denied")
  ```
- audit JSONL 파일에 모든 호출/거부 기록 (`poc/executor_agent/audit.jsonl`)

### 5. Pipeline 부팅 + 메시지 1턴
- `poc/executor_agent/run.py`:
  ```python
  manifest = EnvironmentManifest.load("manifests/gapt_default.v0.json")
  credentials = build_credentials(...)
  pipeline = await Pipeline.from_manifest_async(manifest, credentials=credentials)
  pipeline.on("*", lambda evt: jsonl_audit.write(evt))
  result = await pipeline.run("Use the gapt_hello tool with name='world' and report what you got")
  print(result)
  ```
- 기대: CLI가 `mcp__gapt__gapt_hello` 호출 → bridge → host → "hello, world" 결과 → CLI가 그것을 자연어로 요약해 응답.
- 두 번째 메시지: `"Now try gapt_unsafe with cmd='rm -rf /'"` → CLI가 도구 호출 → bridge → host → PolicyEngine 거부 → CLI가 거부 메시지를 사용자에게 표시.

### 6. HookRunner PRE_TOOL_USE 검증
- `poc/executor_agent/policy_hook.py`:
  ```python
  from geny_executor.hooks import HookRunner
  runner = HookRunner()

  async def pre_hook(event):
      audit.log_pre(event)
      if event.tool_name.startswith("Bash"):
          raise ToolFailure("Bash blocked in PoC", code="exec.tool.access_denied")

  runner.on_pre_tool_use(pre_hook)
  pipeline.attach_runtime(hook_runner=runner)
  ```
- **중요 검증**: `claude_code_cli` 사용 시 *CLI 내부* 도구 호출은 Stage 10을 거치지 않으므로 *위 PRE_TOOL_USE는 발화 안 함*. 따라서 두 경로의 게이트 분리를 *명확히 보여줘야* 함:
  - (A) CLI built-in `Bash`는 `settings_path` allow-list만 거부 가능 (PoC manifest에서 `Bash` 미허용 → CLI가 자체 거부)
  - (B) `mcp__gapt__*`는 bridge 안에서 PolicyEngine 재평가 가능
- PoC 안에서 (A)와 (B) 각각의 거부 경로를 *서로 다른 콘솔 메시지*로 보여주고 `decision_two_layer_policy.md`에 기록.

### 7. 에러 코드 4종 재현
- `exec.cli.binary_not_found`: `binary_path: "/nonexistent/claude"` → 실패 → audit에 code 기록
- `exec.cli.auth_failed`: 잘못된 API key + OAuth 없음 → 실패 → 코드 기록
- `exec.cli.timeout`: `timeout_s: 1.0` + 긴 요청 → 실패 → 코드 기록
- `exec.cli.permission_denied`: `settings_path` 비움 + MCP 호출 → CLI permission 거부 → 코드 기록
- 결과를 `poc/executor_agent/error_codes_reproduced.md`에 표 형식으로

### 8. 비용/토큰 누계 표시
- `result.total_cost_usd`, `result.usage.input_tokens`, `result.usage.output_tokens` 출력
- PoC manifest의 `cost_budget_usd: 0.10` 초과 시 `GuardRejectError(code="exec.stage.guard_rejected")` 발생 검증

### 9. 통합 스크립트
- `poc/executor_agent/scripts/run_poc.sh`:
  ```bash
  set -euo pipefail
  python -m poc.executor_agent.host &   # 호스트 backend (unix socket)
  HOST_PID=$!
  trap "kill $HOST_PID" EXIT
  sleep 1
  uv run python -m poc.executor_agent.run
  ```

## 산출물
```
poc/
├── executor_agent/
│   ├── README.md
│   ├── manifests/gapt_default.v0.json
│   ├── credentials.py
│   ├── host.py                     # 도구 카탈로그 + dispatch + audit
│   ├── policy_hook.py              # PRE_TOOL_USE 더미 정책
│   ├── run.py                      # Pipeline.from_manifest_async 진입
│   ├── audit.jsonl                 # 실행 시 생성됨
│   ├── decision_two_layer_policy.md
│   ├── error_codes_reproduced.md
│   └── scripts/run_poc.sh
└── mcp_bridge/
    ├── README.md
    └── server.py                   # ~150 LoC stdio JSON-RPC loop
analysis/2026XXXX_executor_integration_findings.md
```

## 검증 시나리오
1. `bash poc/executor_agent/scripts/run_poc.sh` → 첫 응답 30초 내, 콘솔에 `mcp__gapt__gapt_hello` 호출 트레이스 + "hello, world" 결과 표시.
2. `audit.jsonl`에 (a) Pipeline 시작 (b) Stage 6 API 호출 (c) `tool.call_start/complete` for `mcp__gapt__gapt_hello` (d) Stage 7 token usage (e) Pipeline 완료 이벤트 모두 기록.
3. `gapt_unsafe` 시나리오: CLI가 도구 호출 → bridge에서 거부 → CLI가 거부 메시지 처리 → audit에 `exec.tool.access_denied` 기록.
4. CLI built-in `Bash` 시나리오: manifest에 Bash 미허용 → CLI 자체 거부 → audit에 `exec.cli.permission_denied` 기록. (※ Stage 10 PRE_TOOL_USE 훅 발화 *안 함* 확인)
5. `cost_budget_usd: 0.001`로 manifest 수정해 짧은 대화 후 `exec.stage.guard_rejected` 발생.
6. 4개 에러 코드 시나리오 모두 자동 재현 가능 (`pytest poc/executor_agent/tests/`).

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|
| `claude` CLI 버전이 stream-json 출력 형식 변경 | 큼 — bridge 깨짐 | `docs/claude_code_cli.md` "argv 자동 호환" 신뢰 + 발견 즉시 geny-executor에 issue |
| MCP stdio bridge가 큰 응답에서 buffer 오버플로 | 중 | 청크 단위 stream + 응답 크기 cap |
| OAuth path가 컨테이너 안에서 keychain 접근 불가 | 중 | 첫 PoC는 호스트에서 직접 (컨테이너 안 OAuth는 M1-E2 별도 이슈) |
| Anthropic API 비용이 PoC 중 누적 | 작음 | `cost_budget_usd: 0.10` cap, 무한 루프 방지 `max_iterations: 5` |
| Stage 10 PRE_TOOL_USE 훅이 *발화하는지/안 하는지*가 헷갈림 | 중 — 설계 오해 가능 | PoC 보고서에 "두 게이트 분리"를 *figure*로 그려서 §4.6 / §9.2.3 보강 근거로 사용 |
| HookRunner `attach_runtime` 호출 위치/순서 실수 | 중 | `docs/hooks.md` 예제 그대로 따름, 테스트로 hook 호출 횟수 assert |

## 관련 docs
- [`../../04_llm_agent_layer.md`](../../04_llm_agent_layer.md) §4.3 manifest, §4.5 MCP, §4.6 PolicyEngine, §4.10 에러 코드
- [`../../09_security_authz_observability.md`](../../09_security_authz_observability.md) §9.2.3 PolicyEngine 2단계 게이트
- geny-executor docs/claude_code_cli.md, docs/mcp.md, docs/hooks.md, docs/error_codes.md
- [[reference_geny_executor_v2_1]]

## 완료 후 보고할 학습
- SeaweedFS Mount 위에서 `claude` CLI 작업 디렉토리(`workspace_root`)가 정상 동작하는가
- MCP stdio bridge 처음 응답 latency (서브세컨드 목표)
- `tool_use` drop 동작이 실제로 일어나는지 (`feed()` 스트림 트레이스)
- `extras["settings_path"]` 인라인 JSON이 실제로 CLI에 잘 전달되는지
- `mcp__gapt__*` 도구 이름이 CLI의 LLM에게 *어떻게 노출*되는지 (system message에 자동 포함되는지)
- 다음 cycle (M1-E2)에서 SDK provider도 지원할 때 추가 작업 추정
