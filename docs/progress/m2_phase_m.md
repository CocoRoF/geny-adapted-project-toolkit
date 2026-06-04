# Progress — M2 Phase M (post-L deep-review remediation)

> Plan: [`../plan/m2_phase_m.md`](../plan/m2_phase_m.md)
> Status: in_progress (started 2026-06-01)

## Sub-phase tracking

| ID | 범위 | Status | 노트 |
|---|---|---|---|
| M.1 | P0 memory bounds via Settings | done | 5 Settings 필드, SessionRegistry LRU+idle sweep, rehydrate/full_replay/state.messages cap, 4 신규 테스트 모두 통과 |
| M.2 | P1 override revert + 핵심 테스트 3건 | done | per-invoke override 가 `pipeline._config.model.*` 를 mutate, baseline snapshot + `clear` 리스트, `PATCH /sessions/{id}/overrides`, ChatPanel pill clear → 즉시 revert, 7 신규 테스트 |
| M.3 | P1 SSE route test (uvicorn fixture) | pending | — |
| M.4 | P2 sessions.py split + Depends 추출 | pending | — |
| M.5 | P2 i18n + env target_config edit UX | pending | — |
| M.6 | P3 markdown 외부 링크 + tooltip + picker overflow | pending | — |
| M.7 | Tool-call history rehydration | pending | — |
| M.8 | Hook 도입 + persist stage 결정 | pending | — |
| M.9 | Syntax highlight + bare permission cleanup | pending | — |

## Timeline

- **2026-06-01** — cycle 개시. deep-review 결과 4 priority 그룹 (20+ 항목) 식별,
  사용자 지시 "P0 config 화 + 나머지 권장값으로 전부 완벽 진행". umbrella plan card 작성,
  9 sub-phase 분해, 00_master_plan.md 인덱스 추가.
- **2026-06-04** — M.2 완료. deep-review 가 지적한 "per-invoke override 가 실제로
  안 먹는다"는 잠재 버그를 확정 + 수정. geny-executor 2.1 의 `Pipeline._init_state`
  가 매 `run_stream` 시작 때 `_config.apply_to_state(state)` 를 호출하여
  `state.model` 을 manifest 값으로 OVERWRITE 하기 때문에, GAPT 의 기존
  `state.model = payload.model` 경로는 1회용도 아닌 무력 코드였음.
  새 경로: `pipeline._config.model.*` 를 mutate → `apply_to_state` 가 override
  값을 그대로 state 에 복사. `SessionRuntime` 에 baseline snapshot
  (`_baseline_model`, `_baseline_thinking_*`) 과 `apply_per_invoke_overrides()`
  도입, 첫 override 시 lazy capture. `InvokeRequest.clear: list[str]` 추가
  ("model", "thinking_enabled", "thinking_budget_tokens", "thinking" 별칭).
  Reset wins over set 정책. 신규 `PATCH /sessions/{id}/overrides` 엔드포인트
  (`OverridePatch` → `OverrideSnapshot`) — 채팅 UI 의 pill clear 가
  사용자 메시지 입력 없이도 즉시 manifest baseline 으로 revert 할 수 있게.
  ChatPanel 의 `onPickModel(null)` / `onPickThinking(null)` 가 자동으로 patch
  호출. API client 에 `patchSessionOverrides()` + `clear` 필드 추가.
  신규 테스트: streaming 단 5건 (mutates_pipeline_config_not_state,
  clear_restores_baseline, clear_wins_over_set, budget_implies_enabled,
  noop_without_baseline_capture) + sessions 단 2건 (_full_replay combine,
  rehydrate round-trip restores messages). 148 pass 회귀 0건.
- **2026-06-04** — M.1 완료. `Settings` 에 5개 `GAPT_SESSION_*` knob 추가
  (`cache_size=50`, `idle_eviction_s=1800`, `max_rehydrate_events=1000`,
  `max_messages_in_state=50`, `max_stream_replay_events=2000`).
  `SessionRegistry` 에 LRU + idle-sweep 도입, `SessionRuntime.touch()` 호출 위치
  (invoke / SSE subscribe / get / register) 모두 와이어업.
  `_runtime_or_rehydrate` 의 `session_events` SELECT 에 LIMIT 추가 (DESC + reverse),
  `_full_replay` 도 동일 패턴. `_drive_pipeline` 가 매 invoke 직전 `state.messages`
  의 oldest 부분을 trim. 컨테이너 `_make_session_registry(settings)` 팩토리 + lifespan
  `start_sweep()` 호출. 신규 테스트 4건 (`test_registry_lru_evicts_oldest_on_overflow`,
  `test_registry_idle_sweep_evicts_past_window`, `test_registry_touch_keeps_active_session_warm`,
  `test_drive_pipeline_trims_state_messages_to_cap`) 모두 통과,
  기존 agent/session 141 테스트 회귀 없음.

## Drift

(cycle 종료 시 plan 과 실제 사이의 차이 정리)
