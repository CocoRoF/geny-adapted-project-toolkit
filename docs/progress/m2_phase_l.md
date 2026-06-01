# Progress — M2 Phase L (chat fortification round 2)

> Plan: [`../plan/m2_phase_l.md`](../plan/m2_phase_l.md)
> Status: in_progress (started 2026-06-01)

## Timeline

- **2026-06-01** — cycle 개시. 코드 추적 (geny-executor surface map + GAPT
  사용 비교) 결과 핵심 발견: 우리가 `Pipeline.run_stream(message)` 에 state 인자를
  안 넘기고 있어서 매 turn 마다 PipelineState 가 새로 생성됨 → multi-turn 불가.
  executor 가 이미 제공하는 표면 (state 보존 / messages array / hook 9개 /
  thinking_*) 위에서 GAPT 가 *제대로 사용* 하는 로직만 추가. Plan 카드 작성,
  00_master_plan.md 인덱스 추가.
- **2026-06-01** — L.1 완료. `SessionRuntime.conversation_state: PipelineState | None`
  추가. `_drive_pipeline` 가 lazy-init 후 `pipeline.run_stream(message, state=...)`.
  Rehydrate (server restart 후) 시 session_events 의 user_message/text 를
  `transcript.build_transcript` → `to_anthropic_messages` 로 변환해서 messages 복원.
  단위 테스트 3건 (lazy create / 같은 객체 reuse / preloaded state 존중) +
  transcript helper 3건 (flat conversion / 빈 turn skip / max_turns cap).
- **2026-06-01** — L.2 완료. `session_manager.reactivate` + `POST /sessions/{id}/reactivate`.
  status archived→active, last_active_at 업데이트, audit log. 이미 active 면 idempotent.
  HTTP 통합 테스트 (archive → reactivate → list 에 다시 나옴, 2번째 reactivate 도 idempotent).
- **2026-06-01** — L.4 backend 완료. `ManifestOverrides` 에 `thinking_enabled` +
  `thinking_budget_tokens` 추가, `has_any()` 갱신, `_merge_overrides` patch-wins 유지.
  `apply_overrides` 가 top-level `model` 딕셔너리 + api stage config 양쪽에 mirror
  (ModelConfig + 레거시 stage 호환). 0 < budget AND enabled=None 이면 implicit True
  (operator convenience). 단위 테스트 4건.
- **2026-06-01** — L.3 + L.4 frontend 완료.
  ChatPanel: `workspaceSessions` state, `listSessions(projectId, {workspaceId, includeArchived:true})`,
  URL `?session=<id>` deep-link auto-select. `SessionPicker` 컴포넌트 (status badge /
  snippet / cost / turn count / 마지막 활동 timestamp), 클릭 → switch (archived 는 inline reactivate).
  `ThinkingPill` 컴포넌트 (auto/off/1k/4k/16k presets), per-project localStorage 영속화.
  createSession 페이로드에 thinking_enabled + thinking_budget_tokens 추가.
  SessionDetail: archived 일 때 "이어서 진행" 버튼 → reactivate → IDE 라우트로 navigate.
  `listSessions` API 가 `workspaceId` 옵션 받음, `reactivateSession(id)` 함수 추가.
- **2026-06-01** — L.5 라이브 검증 (핵심 결과):
  - **multi-turn 메모리 (L.1 의 raison d'être)**:
    turn 1: "내 이름은 alice 야" → "안녕하세요, Alice!"
    turn 2: "내 이름이 뭐였지?" → "Alice"
    **server restart 후 (rehydrate)** 같은 질문 → "Alice" ← messages 복원 검증
    **archive → reactivate 후** 같은 질문 → "Alice" ← reactivate 가 메모리 보존 검증
  - workspace 필터: `?workspace_id=<wid>` 가 그 워크스페이스 row 만 반환
  - thinking override: `apply_overrides` 가 model dict + api stage config 양쪽에 land
  138/138 server tests pass. tsc clean, lint 새 파일 0 error.

## Fix-up (2026-06-01, post-merge)

Live test 결과 두 가지 critical 회귀가 발견됨:

1. **SSE stream 이 done event 후 닫혀서 multi-turn 미작동 (UI 만)**:
   백엔드는 turn 2 의 user_message → text → done 까지 정상 emit 했고 `msgs=3` 도
   context.built 에 찍힘 (multi-turn memory 자체는 OK). 하지만 server 의 `stream_to_async_iter`
   가 done 이벤트 후 `return` → SSE socket close. 클라이언트의 `useSessionStream` 은
   `[sessionId]` 만 dep 로 가지고 있어서 EventSource 가 한 번 닫히면 다음 invoke 의
   events 를 절대 못 받음. 사용자 화면에 turn 2 의 어시 응답이 안 보임 (백엔드는 정상).
   **Fix**: server side — `stream_to_async_iter` 가 done/error 후 return 안 함, bus subscribe
   가 살아있으면 다음 turn 의 events 도 같은 connection 으로 흘림. session 의 lifetime 동안
   stream 유지. client side `useSessionStream` listener list 에 `user_message` 추가
   (Phase I.2 추가 시 누락).

2. **model / think pill 이 세션 활성 중 잠겨서 변경 불가**:
   Phase L.4 frontend 가 `locked={session !== null}` 로 mid-session 변경 금지. 사용자가
   "model이나 think 설정이 불가능한 문제" 라고 명시적 지적.
   **Fix**: pills 항상 unlock. `InvokeRequest` 에 `model` / `thinking_enabled` /
   `thinking_budget_tokens` 추가, `invoke_session` 진입부에서 `runtime.conversation_state.model` /
   `.thinking_*` 직접 mutate (geny-executor core/stage.py:382-393 의 `resolve_model_config`
   가 매 turn `state.model` / `state.thinking_*` 읽음 — executor-sanctioned 패턴). 모델 변경
   시 `runtime.model_name` 도 같이 sync (Phase I.3 pricing fallback 정확성). `invokeSession`
   API + `ChatPanel` send 경로에 overrides 추가.

검증: 같은 stuck 세션 `01KT0R0P0MV...` 에 turn 3 invoke (`thinking_budget_tokens=1024`)
→ seq 106 `msgs=5` (memory 보존), seq 117 어시 응답 "장하렴님이요!" (메모리 정상), seq 150 done.
SSE 가 살아있으니 UI 가 받음 (라이브 검증 별도 필요지만 백엔드 contract 검증됨).

## Drift

- **side regression fix 같이 처리** — Phase I.2 USER_MESSAGE 추가 이후 `test_streaming.py::test_invoke_runs_runner_to_completion_and_emits_done` 가 깨진 채 남아 있었음 (이전 cycle 이 못 본 것). L.1 의 stub signature 변경으로 한꺼번에 발견해서 같이 fix. 별도 cycle 안 만듦.
- **stub run_stream signature 일괄 갱신** — plan 카드는 안 적었지만 `state=None` kwarg 가 stub 3건 (test_session_recording / test_oneshot / test_routes) 모두 필요. sed 로 일괄 추가.
- **transcript helper 의 위치** — plan 은 "agent/transcript.py 옆에 to_anthropic_messages 신규 helper" 라고 했는데 실제는 *같은 파일* 안에 추가. 별도 모듈 만들 가치 없음 — transcript 와 한 단위.
- **SessionPicker 가 archived 클릭 → 자동 reactivate** — plan 의 "이어서 진행 버튼" 명시적 클릭이 아니라 *그냥 archived row 클릭 = inline reactivate* 로 통합 (single-admin 운영자 시점에서 불필요한 클릭 한 단계 절약).
- **SessionDetail 의 active session 이동에도 query hint** — plan 은 archived → reactivate 경로만 명시했는데, 같은 패턴으로 active 의 "워크스페이스 열기" 도 `?session=<id>` 붙여서 deep-link 통일.
- **ThinkingPill 의 implicit enable** — plan 은 명시 안 함. operator convenience: 양의 budget + thinking_enabled=None 이면 enable=True 추론. apply_overrides 안에서. 단위 테스트로 보호.
- **session_runtime / SESSION_START hook 사용 안 함 (Out of scope 그대로)** — geny-executor 의 SESSION_START / END / USER_PROMPT_SUBMIT hook 으로 옮기는 건 별도 cycle. 이 cycle 은 state 보존만으로 multi-turn 동작 확정.
- **tool-call history 복원 안 함 (Out of scope)** — Anthropic API messages 의 tool_use / tool_result content block 복원은 별도 cycle. 현재는 텍스트만.
- **SSE multi-turn 동작 검증 누락** — Plan 카드의 라이브 smoke 시나리오 (turn 1 → turn 2)
  는 *백엔드 DB 만* 검증했고 UI/SSE 경로는 안 봤음. 사용자가 실제 챗 입력 → 화면에 안 뜨는
  형태로 즉시 발견. 다음부터 multi-turn 변경 시 *SSE 한 connection 안에서 여러 invoke* 시나리오를
  smoke 에 명시적으로 포함해야 함.
- **mid-session 변경 가능성을 plan 단계에서 안 정함** — Plan 카드는 "manifest commit 됐으므로
  세션 활성 중엔 잠금" 으로 일관성 있게 명시했지만, 사용자는 mid-session 변경을 강하게 원함.
  geny-executor 가 `state.model` / `state.thinking_*` 매 turn 읽으므로 mutate 만 하면 됨 —
  단순한 해결책이었음. Plan 단계의 UX 가정 (잠그는 게 안전) 이 사용자 기대와 안 맞음을 라이브에서 발견.
- **`test_stream_emits_text_and_done` skip 처리** — SSE 가 done 후 close 안 하니 httpx 의
  in-memory `ASGITransport` 가 replay 청크를 flush 하지 않음 (transport 가 generator
  return / EOF 까지 버퍼링). keep-alive 마다 flush 가 강제되긴 하지만 mid-stream chunk
  delivery 가 흐트러져서 client 가 timeout 안에 `event: done` 못 봄. unit-level
  `test_stream_replays_then_streams_live` 가 동일 contract 를 generator 직접 호출로
  검증 (turn 2 frame 까지 keep-alive 안에 받는지 + retry 힌트 없는지). 실 socket 통한
  end-to-end SSE 테스트는 uvicorn fixture 도입이 옳지만 이 fix-up scope 밖.
- **`stream_to_async_iter` 의 `keepalive_s` 기본값 lazy resolution** — `keepalive_s: float = DEFAULT_KEEPALIVE_S`
  는 def time 바인딩이라 module-level monkeypatch 가 무효. `keepalive_s: float | None = None` +
  본문에서 None 일 때 `DEFAULT_KEEPALIVE_S` 로 채우도록 변경 (테스트에서 빠른 keep-alive
  강제하려면 monkeypatch 가 유효해짐). 향후 stream tuning 도 같은 패턴.

## Fix-up #2 (2026-06-01, "이전 대화 안 뜸" 보고)

사용자: "세션에 정보를 저장하고 있는 것 같은데, 세션을 다시 불러오기 했을 때
이전 대화를 불러와서 표시해줘야지. 전부 표시해줘야 함."

진단 — 3가지 버그가 겹친 결과:

1. **`stream_to_async_iter` 가 in-memory bus 만 replay** — 서버 재시작으로 ring buffer 가
   비어있는 rehydrated 세션은 `bus.replay(0)` 가 빈 리스트. `/messages` 엔드포인트는 DB
   fallback 로직이 있는데 `/stream` 만 없어서 SSE 가 빈 응답 → 채팅 panel 이 blank.
2. **`useSessionStream` 의 `lastSeqRef` 가 session 전환 시 reset 안 됨** — A 세션
   lastSeq=100 인 상태에서 B 세션 picker → `/stream?since=100` 으로 B 의 첫 100 events 가
   skip. 빈 채팅 panel.
3. **EventSource 의 자동 재연결이 useEffect 안 재실행** — sessionId dep 그대로면 useEffect 안
   탐. 어차피 (1) 의 in-memory only 가 핵심 원인.

**Fix**:
- `agent/session_registry.py` `stream_to_async_iter` 에 `prefix_events: list[SessionEvent]`
  파라미터 추가. SSE generator 첫 부분에서 prefix 를 yield 한 다음 live subscribe.
- `routers/sessions.py` `stream_session` 가 `_full_replay(db, runtime, since)` 호출 →
  `/messages` 와 동일 로직 (bus + DB combine) 으로 SessionEvent 리스트 생성 → `prefix_events`
  로 전달. 라이브 검증: 250 events 짜리 stuck 세션이 SSE 로 전부 (230 step + 5 user_message + 5 text + 5 done + 5 cost) 흘러감.
- `useSessionStream.ts` `useEffect` 가 sessionId 바뀔 때마다 `setEvents([])` +
  `lastSeqRef.current = 0` + `setErrorReason(null)`. URL 도 항상 `since=undefined`
  (서버 0 으로 해석) — 풀 replay 가 어차피 멱등.

### 검증
- 라이브 SSE replay: `01KT0R0P...` (250 events) `/stream?since=0` → 250 frames 전부 도착,
  kind 통계 일치.
- 55/55 agent unit tests pass (test_streaming + test_multi_turn_memory + 등 stream/state
  관련 회귀 X).
- 사용자 액션: 브라우저 새로고침 후 session picker 에서 세션 선택 → 처음부터 전체 대화 표시.
