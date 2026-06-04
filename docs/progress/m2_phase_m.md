# Progress — M2 Phase M (post-L deep-review remediation)

> Plan: [`../plan/m2_phase_m.md`](../plan/m2_phase_m.md)
> Status: in_progress (started 2026-06-01)

## Sub-phase tracking

| ID | 범위 | Status | 노트 |
|---|---|---|---|
| M.1 | P0 memory bounds via Settings | done | 5 Settings 필드, SessionRegistry LRU+idle sweep, rehydrate/full_replay/state.messages cap, 4 신규 테스트 모두 통과 |
| M.2 | P1 override revert + 핵심 테스트 3건 | done | 3 root-cause fix: (1) `pipeline._config.model.*` mutate (state.model wipe 우회), (2) ChatPanel 매 invoke 풀 의도 전송 (명시값 OR `clear`), (3) baseline = manifest bundled api model (admin-pref 적용 전 raw). PATCH /overrides 엔드포인트. 8 신규 테스트. 라이브 검증 완료. |
| M.3 | P1 SSE route test (uvicorn fixture) | done | `tests/_helpers/uvicorn_server.py` (ephemeral port + lifespan-on + ws=none). 2 신규 테스트: `test_stream_emits_text_and_done_via_uvicorn`, `test_stream_continues_across_turn_via_uvicorn`. 151 pass / 0 skip. |
| M.4 | P2 sessions.py split + Depends 추출 | done | `SessionAccess` dataclass + `get_session_access()` sub-Depends. 8개 route handler 서명이 8-Depends 컬럼 → 1줄. `_runtime_or_rehydrate(session_id, access=...)` 시그니처 단순화. Lint 통과, 151 pass. File split 은 import-chain 리스크 대비 이득 적어 deliberate-defer. |
| M.5 | P2 i18n + env target_config edit UX | done | SessionsHistory + SessionDetail 의 hardcoded ko 13개 → `useI18n()` 키. ko/en 양쪽 추가. `formatRelative()` 가 `t` 를 파라미터로 받도록 변경. EnvironmentEditor 에 `RawConfigPreview` (collapsible read-only JSON) 추가. TS clean. |
| M.6 | P3 markdown 외부 링크 + tooltip + picker overflow | done | (1) `MarkdownText` 가 DOMPurify hook 으로 external `<a>` 에 `target="_blank" rel="noopener noreferrer"` 자동 부여. (2) `CostModal` cache_write/cache_read 라벨에 `title` 툴팁 + dotted underline 시각 단서. (3) `SessionPicker` 의 truncate 가 실제로 작동하도록 `min-w-0` + `shrink-0` 정리. TS clean. |
| M.7 | Tool-call history rehydration | done | `to_anthropic_messages(..., include_tool_blocks=True)` 가 tool_use + tool_result content blocks 를 정확한 순서 (user → assistant(tool_use) → user(tool_result) → assistant(text)) 로 재구성. `_stringify_tool_output()` 가 dict/list/None 도 안전하게 처리, 8KB cap. 4 신규 테스트, 155 pass. |
| M.8 | Hook 도입 + persist stage 결정 | done | (1) gapt_default.json 의 s20_persist 를 `active: false` 로 명시 — NoPersister no-op 인데 active:true 인 게 매니페스트 읽는 운영자에게 misleading. `_gapt_note_M8` 으로 회복 경로 기록. (2) runner.py 에 나머지 11개 executor hook 미연결 사유 명시 (SessionEventBus 가 같은 lifecycle 을 이미 cover, 가설적 integration 위한 scaffolding 금지). 155 pass. |
| M.9 | Syntax highlight + bare permission cleanup | done | (1) `highlight.js/lib/common` + `marked-highlight` 도입, `markedHighlight` extension 으로 fenced code 블록에 토큰 클래스 부여, github-dark 테마 import. ~40 언어, 폴백은 plaintext. (2) `tests/conftest.py` 에 session-scoped autouse 픽스처로 `GAPT_WORKSPACE_BARE_ROOT` 를 `tmp_path_factory` 경로로 override → caddy/e2e 등 워크스페이스-생성 경로 테스트에서 `/var/lib/gapt-bare` PermissionError 해소. 155 pass + 이전 PermissionError 실패 해소. |

## Timeline

- **2026-06-01** — cycle 개시. deep-review 결과 4 priority 그룹 (20+ 항목) 식별,
  사용자 지시 "P0 config 화 + 나머지 권장값으로 전부 완벽 진행". umbrella plan card 작성,
  9 sub-phase 분해, 00_master_plan.md 인덱스 추가.
- **2026-06-04 (M.2 라이브 검증 후 보강)** — 사용자 라이브 테스트에서 inherit
  선택 시 여전히 admin pref 모델 (opus) 로 가는 두 번째 버그 발견. 진단:
  (a) 프론트엔드가 `modelOverride=null` 일 때 payload 에 model 필드를 누락 →
  서버가 "변경 없음" 으로 해석, 기존 override 유지. (b) baseline 이 `_config.
  model.model` (= admin pref 적용된 값) 으로 lazy-capture 되어, pill 라벨이
  promise 한 "(uses sonnet)" 과 불일치. 수정: ChatPanel 이 매 invoke 마다 풀
  의도를 보냄 (`model: X` 또는 `clear: ["model"]`), 그리고 `env_service.
  bundled_api_model(env_id)` 가 raw 매니페스트의 stages[api].config.model 을
  추출 → `_build_runtime_from_handle` 에서 SessionRuntime 의 `_baseline_model`
  로 pre-set. `_capture_baseline` 은 pre-set 값을 보존. 신규 테스트
  `test_clear_reverts_to_preset_bundled_baseline_not_live_config` 추가.
  사용자 라이브 검증 "정상" 확인.
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
