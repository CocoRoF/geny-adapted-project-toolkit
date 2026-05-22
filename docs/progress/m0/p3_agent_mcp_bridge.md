# M0-P3: 에이전트 + MCP bridge PoC — 진행 기록

> Plan: [`../../plan/m0/p3_agent_mcp_bridge.md`](../../plan/m0/p3_agent_mcp_bridge.md)
> Status: **in_progress**
> Started: 2026-05-23
> Owner: gkfua00 (CocoRoF)
> Depends on: ✅ M0-P1 (`a4de305`), ✅ M0-P2 (`f468b15`)

## 진입 조건 검증

- [x] M0-P1 + M0-P2 통과, CI/pre-commit 그린
- [x] `claude` CLI 2.1.126 호스트 설치 (`/home/hrjang/.local/bin/claude`)
- [x] OAuth credentials cached at `~/.claude/.credentials.json` (subscription path — `ANTHROPIC_API_KEY` env 없음)
- [x] geny-executor 2.1.0+ PyPI 의존 (server/pyproject.toml에 이미 명시)
- [x] [[reference_geny_executor_v2_1]] 일독 — 21단계, claude_code_cli provider, MCP 2 boundary, HookRunner, exec.*.* 코드 모두 숙지

## Plan 카드 update (M0-P2 시점에 확인된 환경 변경 반영)

- runtime base는 Ubuntu 24.04 (noble) — `docs/06` Dockerfile 변경 (PR3 시점에 적용 완료)
- Sysbox 0.7.0 (PR2 KI-1 fix로 inner dockerd compose 작동) — 본 PR 진입 시점에 이미 호스트에 설치
- docker-ce는 inner/host 모두 29.2.1 pin (M0-P2 PR4)
- SeaweedFS 0.7.0 호환 weed 클라이언트 already in runtime image

## PR 진행 로그

### PR 1 — runtime에 claude CLI 동봉 + sandbox 부팅 + auth 경로 (✅ 완료 — `193276e`)

- `poc/executor_agent/` 부트스트랩: pyproject.toml (`geny-executor>=2.1.0`), credentials.py (`build_credentials` — OAuth + API key 둘 다), manifests/gapt_default.v0.json (21 stage + `claude_code_cli`)
- 호스트의 `~/.claude/.credentials.json` (OAuth subscription) 경로 통과 — `ANTHROPIC_API_KEY` 없는 환경에서도 부트
- DoD 추적: `poc/executor_agent/` 산출물 ✅

### PR 2 — `gapt_default.v0.json` manifest + Pipeline.from_manifest_async smoke (✅ 완료 — `5b14ae9`)

- `run.py` — manifest dict → `EnvironmentManifest.from_dict()` → `Pipeline.from_manifest_async(manifest, credentials=…)` (문서의 `.load()`는 미존재 메서드였음, code-first 확인 후 정정)
- 프롬프트 "Hello! What's 2 + 2? Reply in one short sentence." → **"2 + 2 = 4."**
- `cost_usd=$0.041`, `elapsed_s=3.84`, 21 stage 진입/탈출 + `pipeline.start` / `pipeline.complete` 모두 audit.jsonl에 캡처
- `EventBus.on("pipeline.*"/"stage.*"/"api.*"/"tool.*")` 4종 구독 동작 확인
- DoD 추적: `claude_code_cli` provider "Hello, what's 2+2?" 응답 ✅, `Pipeline.from_manifest_async()` 부팅 ✅, JSONL 캡처 부분(stage/pipeline 레벨) ✅, 비용/elapsed 표시 ✅

### PR 3 — MCP stdio bridge (~110 LoC) + 호스트 도구 카탈로그 + CLI MCP wrap (✅ 완료 — *this commit*)

- `poc/mcp_bridge/server.py` (~110 LoC, MCP SDK 기반 stdio server)
  - `gapt_hello(name)` — happy-path echo
  - `gapt_unsafe(cmd)` — 항상 `exec.tool.access_denied` 텍스트 응답
  - `GAPT_BRIDGE_AUDIT` 환경변수로 bridge_audit.jsonl 경로 주입
- `poc/executor_agent/run_mcp.py` — mcp_config 빌드 (`uv run --project <bridge> python <bridge>/server.py`) + `settings_path` (`mcp__gapt` + Read/Glob/Grep 허용)
- 1차 시도: 프롬프트에 `cmd='rm -rf /'` 리터럴 포함 → 클로드 안전 필터가 tool 호출 전에 거부. PoC 입증 실패.
- 2차 시도: 프롬프트 재작성 (`cmd='ls'` + "이 도구는 항상 거부함 — 거부 응답을 확인하려는 것임" 명시) → **양쪽 tool round-trip 성공**:
  - bridge_audit.jsonl: `tools/list` → `tools/call gapt_hello` → `tools/call.ok gapt_hello` → `tools/call gapt_unsafe` → `tools/call.denied gapt_unsafe (exec.tool.access_denied)`
  - LLM 최종 응답: 두 tool의 결과를 정확히 요약 (happy-path 응답 + policy-denial 응답)
  - `cost_usd=-$0.0717` (OAuth subscription credit adjustment), `elapsed_s=11.12`
- 알려진 한계: pipeline-side audit_mcp.jsonl은 `stage.*` + `pipeline.*`만 캡처. `tool.*` 이벤트는 CLI 서브프로세스 내부 MCP 디스패치이기 때문에 pipeline EventBus가 못 봄. → **PR4에서 HookRunner 와이어업**으로 처리할 작업.
- DoD 추적: MCP stdio bridge ✅ (~110 LoC), `mcp__gapt__gapt_hello` 호출 + 응답 ✅, `tool_result.isError`-equivalent 경로 (`exec.tool.access_denied` 텍스트) ✅, EventBus stage/pipeline 캡처 ✅. **HookRunner.PRE_TOOL_USE 베토 + pipeline-side tool 캡처는 PR4로 이월.**

### PR 4 — HookRunner PolicyEngine 2-layer 게이트 검증 (대기)
### PR 5 — exec.*.* 4종 에러 재현 + audit JSONL (대기)
### PR 6 — 통합 스크립트 + M0-P3 종료 (대기)

## DoD 진행

[Plan 카드](../../plan/m0/p3_agent_mcp_bridge.md) DoD 8개 그대로:

- [ ] `poc/executor_agent/` + `poc/mcp_bridge/` 산출물
- [ ] `gapt_default.json` 초안 manifest 1개로 `Pipeline.from_manifest_async()` 부팅 성공
- [ ] `claude_code_cli` provider로 "Hello, what's 2+2?" 응답
- [ ] **MCP stdio bridge** (~150 LoC) — CLI가 `mcp__gapt__gapt_hello` 호출 → host registry → 결과 반환
- [ ] **`HookRunner.PRE_TOOL_USE`** veto 검증 — `ToolFailure(ACCESS_DENIED)` 시 dispatch 거부 + audit 기록
- [ ] `EventBus` 구독으로 `api.*` / `tool.call_start` / `tool.call_complete` 캡처되어 JSONL 기록
- [ ] 비용/토큰 누계가 응답에 표시
- [ ] 에러 시나리오 4개 (`exec.cli.binary_not_found` / `exec.cli.auth_failed` / `exec.cli.timeout` / `exec.cli.permission_denied`) 재현 + audit에 코드 그대로 기록

## Drift (cycle 종료 시 작성)

*(아직 종료되지 않음)*
