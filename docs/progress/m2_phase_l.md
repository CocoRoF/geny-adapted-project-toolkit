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

## Drift

- **side regression fix 같이 처리** — Phase I.2 USER_MESSAGE 추가 이후 `test_streaming.py::test_invoke_runs_runner_to_completion_and_emits_done` 가 깨진 채 남아 있었음 (이전 cycle 이 못 본 것). L.1 의 stub signature 변경으로 한꺼번에 발견해서 같이 fix. 별도 cycle 안 만듦.
- **stub run_stream signature 일괄 갱신** — plan 카드는 안 적었지만 `state=None` kwarg 가 stub 3건 (test_session_recording / test_oneshot / test_routes) 모두 필요. sed 로 일괄 추가.
- **transcript helper 의 위치** — plan 은 "agent/transcript.py 옆에 to_anthropic_messages 신규 helper" 라고 했는데 실제는 *같은 파일* 안에 추가. 별도 모듈 만들 가치 없음 — transcript 와 한 단위.
- **SessionPicker 가 archived 클릭 → 자동 reactivate** — plan 의 "이어서 진행 버튼" 명시적 클릭이 아니라 *그냥 archived row 클릭 = inline reactivate* 로 통합 (single-admin 운영자 시점에서 불필요한 클릭 한 단계 절약).
- **SessionDetail 의 active session 이동에도 query hint** — plan 은 archived → reactivate 경로만 명시했는데, 같은 패턴으로 active 의 "워크스페이스 열기" 도 `?session=<id>` 붙여서 deep-link 통일.
- **ThinkingPill 의 implicit enable** — plan 은 명시 안 함. operator convenience: 양의 budget + thinking_enabled=None 이면 enable=True 추론. apply_overrides 안에서. 단위 테스트로 보호.
- **session_runtime / SESSION_START hook 사용 안 함 (Out of scope 그대로)** — geny-executor 의 SESSION_START / END / USER_PROMPT_SUBMIT hook 으로 옮기는 건 별도 cycle. 이 cycle 은 state 보존만으로 multi-turn 동작 확정.
- **tool-call history 복원 안 함 (Out of scope)** — Anthropic API messages 의 tool_use / tool_result content block 복원은 별도 cycle. 현재는 텍스트만.
