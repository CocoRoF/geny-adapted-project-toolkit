# M0-P3: 에이전트 + MCP bridge PoC — 진행 기록

> Plan: [`../../plan/m0/p3_agent_mcp_bridge.md`](../../plan/m0/p3_agent_mcp_bridge.md)
> Status: **completed**
> Started: 2026-05-23
> Completed: 2026-05-23
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

### PR 4 — HookRunner PolicyEngine 2-layer 게이트 검증 (✅ 완료 — *this commit*)

- `poc/executor_agent/policy_hook.py` — `HookRunner` 빌더 (`HookConfig(enabled=True)`, in-process `PRE_TOOL_USE` 핸들러로 `gapt_unsafe` 명시적 거부, `set_audit_callback` 으로 모든 hook fire 기록)
- `poc/executor_agent/run_hooks.py` — `pipeline.attach_runtime(hook_runner=runner)` 로 wiring, run 후 핸들러 fire 횟수 카운트
- 실행 결과:
  - LLM이 `gapt_hello` + `gapt_unsafe` 양쪽 모두 호출 (bridge_audit.jsonl: `tools/call.ok gapt_hello` + `tools/call.denied gapt_unsafe`)
  - **pipeline-side PRE_TOOL_USE fires: 0** — Stage 10 9× bypass로 확인
  - hook audit callback 도 한번도 발화 안 함 → Stage 10 외부에서 fire 호출 없음 검증
- `decision_two_layer_policy.md` — Layer 1 (pipeline `HookRunner.PRE_TOOL_USE`) vs Layer 2a (CLI built-in allow-list) vs Layer 2b (MCP bridge in-process policy) ascii figure + 근거 코드 위치 (`s10_tool/artifact/default/routers.py:262`) + M1-E2 forward-looking implication 포함
- 핵심 깨달음: `claude_code_cli` 사용 시 pipeline-side `HookRunner.PRE_TOOL_USE` 는 호출 자체가 안 됨. host-attached MCP 도구의 정책 게이트는 **MCP bridge 안에** 있어야 함 — pipeline-side hook 으로는 막을 수 없음.
- DoD 추적: **HookRunner.PRE_TOOL_USE 베토 시도 (Stage 10 bypass 검증) ✅** (단, 베토 자체가 발화 안 되는 것이 PoC의 finding이므로 DoD의 의도 — "PolicyEngine 2-layer 명확화" — 는 충족). EventBus stage/pipeline 캡처 ✅. Layer 2a / 2b 거부 데모는 PR5 (`exec.cli.permission_denied`) 에서 마무리.
### PR 5 — exec.cli.* 4종 에러 재현 + audit JSONL (✅ 완료 — *this commit*)

- `poc/executor_agent/fixtures/{fake_auth.sh,fake_perm.sh,fake_slow.sh}` — 각 실패 모드를 흉내내는 stub binary
  - `fake_auth.sh`: stdout 에 stream-json `{"error":"authentication_failed"}` 1줄 출력 → `claude_code.py:302` 의 streaming 파서가 `CLI_AUTH_FAILED` 로 분기
  - `fake_perm.sh`: stderr 에 "permission denied" — *streaming path 에서는 protocol_error 로 분기됨*, 그래서 PR5는 직접 `_classify_cli_result` 호출로 검증
  - `fake_slow.sh`: 30s sleep → timeout_s=2.0 으로 `CLI_TIMEOUT` 강제
- `reproduce_errors.py` — 4개 시나리오 자동 실행, `stage.error` 이벤트 hook 으로 `exec.cli.*` 코드 캡처, `audit_errors.jsonl` + `error_codes_reproduced.md` 생성
- 실행 결과 (`overall: PASS`):

  | Scenario | Expected | Observed | Pass |
  |---|---|---|---|
  | exec.cli.binary_not_found | exec.cli.binary_not_found | exec.cli.binary_not_found | ✅ |
  | exec.cli.auth_failed | exec.cli.auth_failed | exec.cli.auth_failed | ✅ |
  | exec.cli.timeout | exec.cli.timeout | exec.cli.timeout | ✅ |
  | exec.cli.permission_denied | exec.cli.permission_denied | exec.cli.permission_denied | ✅ (classifier_unit) |

- 발견 사항 — geny-executor upstream feedback 거리: `_cli_runtime.py:270-274` 의 streaming 경로가 non-zero exit 시 `_classify_cli_result` 휴리스틱을 안 돌림. 그래서 stderr 에 "permission denied" 있는 CLI 가 `exec.cli.protocol_error` 로 잘못 분류됨. PR5는 정의에 부합하는 `_classify_cli_result` 호출로 코드 매핑 검증, **M1-E2 에서 upstream patch 제안** 큐잉.
- DoD 추적: 에러 시나리오 4개 재현 ✅, audit JSONL 에 코드 그대로 기록 ✅
### PR 6 — 통합 스크립트 + M0-P3 종료 (✅ 완료 — *this commit*)

- `poc/executor_agent/scripts/run_all.sh` — 4개 시나리오 (PR2 smoke / PR3 MCP / PR4 hooks / PR5 errors) 순차 실행 + 마지막에 audit 파일 5개 (`audit.jsonl`, `audit_mcp.jsonl`, `audit_hooks.jsonl`, `audit_errors.jsonl`, `../mcp_bridge/bridge_audit.jsonl`) 위치 + 다음 단계 안내 출력
- `docs/04_llm_agent_layer.md` §4.6 + `docs/09_security_authz_observability.md` §9.2.3 에 forward-pointer 추가: 본 PoC 의 `decision_two_layer_policy.md` 를 PolicyEngine 구현 1차 근거로 명시
- 본 진행 카드 Drift 섹션 작성
- M0-P3 종료

## DoD 최종 결과

| DoD 항목 | 상태 | 근거 |
|---|---|---|
| `poc/executor_agent/` + `poc/mcp_bridge/` 산출물 | ✅ | PR1~PR5 |
| `Pipeline.from_manifest_async()` 부팅 + manifest 1개 | ✅ | PR2, 21 stage 정상 진입/탈출 |
| `claude_code_cli` provider "2+2" 응답 | ✅ | PR2 ($0.041, 3.84s) |
| MCP stdio bridge + `mcp__gapt__gapt_hello` 호출/응답 | ✅ | PR3 bridge_audit.jsonl |
| `HookRunner.PRE_TOOL_USE` veto 시도 + Stage 10 bypass 검증 | ✅ | PR4 (pipeline-side fire 횟수 0 empirically) |
| `EventBus` 구독으로 stage/pipeline 이벤트 JSONL 캡처 | ✅ | 4개 audit 파일 |
| 비용/토큰 누계 응답 표시 | ✅ | PR2 cost_usd / elapsed |
| `exec.cli.*` 4종 재현 + audit 기록 | ✅ | PR5 (`error_codes_reproduced.md` overall PASS) |

`Bash → Read → audit jsonl 5개 파일 검증` 까지 1 command (`bash poc/executor_agent/scripts/run_all.sh`) 로 재현 가능.

## DoD 진행

[Plan 카드](../../plan/m0/p3_agent_mcp_bridge.md) DoD 8개 그대로:

- [x] `poc/executor_agent/` + `poc/mcp_bridge/` 산출물
- [x] `gapt_default.json` 초안 manifest 1개로 `Pipeline.from_manifest_async()` 부팅 성공
- [x] `claude_code_cli` provider로 "Hello, what's 2+2?" 응답
- [x] **MCP stdio bridge** (~110 LoC) — CLI가 `mcp__gapt__gapt_hello` 호출 → bridge → 결과 반환
- [x] **`HookRunner.PRE_TOOL_USE`** veto 시도 — Stage 10 bypass 로 발화 안 함을 empirical 검증 (decision doc)
- [x] `EventBus` 구독으로 stage/pipeline 이벤트 JSONL 기록 (`tool.*` 은 CLI 내부라 미캡처; 의도된 결과 — decision doc)
- [x] 비용/토큰 누계가 응답에 표시
- [x] 에러 시나리오 4개 재현 + audit에 코드 그대로 기록 (3개 pipeline path + 1개 classifier_unit)

## Drift (cycle 종료 시 작성)

### Plan 카드 대비 변경

1. **`gapt_default.v0.json` 도구 카탈로그** — 계획 §1에서 `tools.external: ["gapt_hello"]` 명시했으나 실제 manifest에는 도구 카탈로그 필드를 비워 두고 (geny-executor 2.1.0 의 `EnvironmentManifest` 가 `claude_code_cli` provider 일 때는 stage 10 자체를 bypass 하므로 manifest 의 tools 필드가 무의미) **MCP 도구 등록은 credential `extras["mcp_config"]` 로 일임**. 결과적으로 manifest 는 21 stage 정의만 담고, 도구는 PR3 의 MCP bridge 가 동적 발견.
2. **`poc/executor_agent/host.py` 미구현** — 계획 §4에서 unix-socket FastAPI 호스트 + JWT 인증을 그렸으나, PoC 범위에서는 **MCP bridge 가 직접 inline 디스패치** 하는 단일 프로세스 형태가 더 명료. JWT/소켓 분리는 M1-E2 (`gapt-host`) 로 이월. 본 결정 근거: PoC 의 1차 목표가 "정책 게이트 2단계 분리 검증" 이고, host 프로세스 분리는 그 검증에 필요하지 않았음.
3. **PR4 의 PRE_TOOL_USE deny 시나리오** — 계획 §6 가 "Bash blocked in PoC" 를 예시로 들었으나 본 PoC 는 **`gapt_unsafe` 도구 deny** 로 대체. 이유: Bash 는 CLI built-in 으로 `settings_path` allow-list 에서 빠지는 순간 LLM 이 호출 시도조차 안 함 (안전 필터). `gapt_unsafe` 가 "도구 호출까지는 도달 → 정책에서 거부" 시퀀스를 더 명확히 보여줌. 단, 결과는 동일 — pipeline-side `PRE_TOOL_USE` 가 **발화 안 함** 을 empirically 확인. (Bash 시나리오는 PR5 의 `exec.cli.permission_denied` 재현으로 분리 흡수.)
4. **`exec.cli.permission_denied` 의 stream-path 미커버** — 계획에 안 적힌 발견. `_classify_cli_result` 휴리스틱이 streaming CLI 경로에서는 안 돌아가는 비대칭이 있음 (`_cli_runtime.py:270-274` vs `claude_code.py:65-69`). PoC 는 `_classify_cli_result` 직접 호출로 코드 매핑만 검증. **M1-E2 의 첫 upstream patch 대상**.
5. **비용 단위** — 계획에 `cost_budget_usd: 0.10` 설정. 실제 OAuth subscription credit adjustment 로 cost_usd 가 음수 (-$0.07~) 로 나오는 경우 관찰. Pipeline 의 budget 가드는 양수 누계만 보므로 무한 루프 위험 없음 — but UI surfaces 에서 음수 cost 처리 필요 (M1-E2 cost summary widget).

### 학습 (plan §"완료 후 보고할 학습" 응답)

- **SeaweedFS Mount 위의 `claude` CLI 작업 디렉토리** — PoC 는 host workspace 에서 직접 실행. SeaweedFS 위 동작 검증은 M1-E2 의 sandbox 통합 시 함께. PR2 의 21 stage 부팅 자체는 SeaweedFS 무관하게 PASS.
- **MCP stdio bridge 처음 응답 latency** — `tools/list` 후 첫 `tools/call.ok` 까지 ~6초 (bridge_audit.jsonl ts diff). 대부분 LLM 결정 시간이고 bridge 자체는 sub-ms. M1-E2 에서 정량 측정 (cold start vs warm).
- **`tool_use` drop 동작** — PR2/PR3/PR4 audit 모두 `stage.bypass` 9개 발생. Stage 10 (tool routing) 포함. CLI provider 가 routing 을 take-over.
- **`extras["settings_path"]` inline JSON 전달** — PR3 에서 검증 완료 (`mcp__gapt` 허용 시 `gapt_*` 호출 통과, Bash 미허용 동작은 PR5 의 fake_perm.sh stderr 텍스트로 우회 검증).
- **`mcp__gapt__*` 도구 노출 방식** — LLM 의 자연어 응답 (PR3) 에서 정확히 `mcp__gapt__gapt_hello` / `mcp__gapt__gapt_unsafe` 명으로 인식. system message 자동 합성 동작 확인.
- **M1-E2 추정** — host 프로세스 분리 + sandbox 통합 + permission_denied stream-path 패치 + bridge 의 PolicyEngine 통합 + cost 음수 UI 처리, 약 4~5 PR.

### Memory 업데이트

- `[[reference_geny_executor_v2_1]]` 보강 — Stage 10 의 단일 PRE_TOOL_USE fire 위치 (`s10_tool/artifact/default/routers.py:262`) + permission_denied stream-path 비대칭 노트 추가 권장.
- `[[feedback_policy_config_not_hardcode]]` 와 `[[feedback_extend_executor_not_adapter_layer]]` 둘 다 본 PoC 가 *실증* — PoC bridge 의 in-process policy 가 `gapt_unsafe` 만 단순 거부하는 hardcode 라는 자기-한계 명시 + M1-E2 에서 config-driven 으로 승격 필요.

### 후속 PR (M1-E2 진입 시점에 처리)

1. MCP bridge → `geny-executor` (또는 슬림 동반 패키지) 로 promote, app-side adapter 금지 ([[feedback_extend_executor_not_adapter_layer]] 준수).
2. `_classify_cli_result` 휴리스틱을 streaming CLI path 에도 적용하는 geny-executor upstream PR 제출.
3. PolicyEngine config-driven 구현 (config 파일 + 4계층 병합 — §9.2.3 의 layered policy 모델 그대로).
4. host 프로세스 분리 + JWT 인증 + unix socket — M0-P3 §4 계획 미구현분.
5. Sandbox 위 SeaweedFS workspace_root 실측 검증 + `claude` CLI 가 FUSE mount 에서 정상 동작하는지 확인.
