# Progress — M2 Phase I (세션 기록 무결성)

> Plan: [`../plan/m2_phase_i.md`](../plan/m2_phase_i.md)
> Status: in_progress (started 2026-06-01)

## Timeline

- **2026-06-01** — cycle 개시. 라이브 DB 진단 (cost_usd=0 / input_tokens=0 두 세션 확인,
  but done event snapshot 에 tokens 6/42 존재) → 3가지 잠복 버그 확정:
  cost_callback 미연결 / USER_MESSAGE event 부재 / model alias "sonnet" → pricing miss.
  Plan 카드 작성, 00_master_plan.md 인덱스 추가.
- **2026-06-01** — I.1 완료. `SessionRuntime.cost_callback` field 추가, `_drive_pipeline` 의
  `token.tracked` 분기에서 호출. router 측 `_build_runtime_from_handle` 가 `_on_cost_update`
  를 runtime.cost_callback 으로 세팅. 효과: tool 없는 채팅도 DB 의 `agent_sessions.cost_usd/
  input_tokens/output_tokens` 즉시 업데이트.
- **2026-06-01** — I.2 완료. `SessionEventKind.USER_MESSAGE` 추가, `_run_with_lifecycle`
  최초 라인에서 `bus.publish(USER_MESSAGE, {"text": message})`. 자동으로 persister 가
  session_events 에 기록. Web 측 `SessionEventKind` 에도 추가, ChatPanel 의 EventRow 에
  `user_message` 렌더 (right-aligned user bubble), allEvents merger 가 optimistic 버블과
  backend user_message 가 같은 text 면 dedupe.
- **2026-06-01** — I.3 완료. `agent/pricing.py` 신규: `_MODEL_ALIASES` 테이블 + `lookup_price`
  (upstream `ALL_PRICING` 위임) + `compute_cost_usd`. `_update_accumulator` 가 executor cost=0
  & tokens > 0 일 때만 fallback 호출. `_build_runtime_from_handle` 가 manifest 의 api 스테이지
  config 로부터 model string 추출 (`_extract_api_model(env_service, env_manifest_id)`).
- **2026-06-01** — I.4 완료. `agent/transcript.py` 신규: `build_transcript` (turn grouping by
  `user_message`) + `render_markdown` + `to_dict`. `GET /_gapt/api/sessions/{id}/transcript?format={json,markdown}`
  엔드포인트. ChatPanel header 에 다운로드 버튼.
- **2026-06-01** — Live smoke OK. 라이브 세션 invoke "What is 3+3?" → DB row cost=$0.012902,
  user_message event 1개, transcript markdown 3 turn 정상 렌더, cost dashboard `$0.0129`.
  단위 테스트 24/24 pass.

## Drift

- **`_extract_api_model` 의 signature 변경** — plan 에서는 `pipeline` 객체만 받기로
  했으나 실제로 geny-executor `Pipeline` 은 manifest 를 attr 으로 노출 안 함 (`stages` 만 들고
  있고 그것도 stage 객체로 변환됨, 원본 dict 보존 X). → 대신 `(env_service, env_manifest_id)`
  를 받아 `env_service.resolve(...)` 로 다시 manifest 를 로드. 부가 호출 한 번 더지만
  manifest 파일 크기 작아서 무시 가능.
- **fallback cost > 토큰 카운트 와 안 맞아 보이는 현상** — 사용자 chat 1 turn 의 cost 가
  $0.012902 인데 displayed input/output_tokens 는 6/6. 원인: executor 의 `token.tracked`
  payload 가 `cache_write` / `cache_read` 도 들고 있고, `compute_cost_usd` 가 이들의 가격을
  *제대로* 더함. CostAccumulator 는 regular input/output 만 노출해서 사용자 입장에선 "왜
  6 토큰인데 $0.013 인가" 의문이 생길 수 있음. → out-of-scope; cache 토큰을
  AgentSession row 에 별도 컬럼으로 노출하는 건 별도 PR (cost 자체는 정확).
- **i18n 새 key 만 추가, 기존 chat / cost translations 손대지 않음** — Plan 의 "i18n 몇 개 키"
  의도 그대로 유지.
- **upstream geny-executor 의 model alias PR 안 함** — plan §Out of scope 명시대로 별도 cycle
  로 미룸. GAPT-side alias 만 add 해도 운영자 입장에서는 cost 가 정상 나옴.
