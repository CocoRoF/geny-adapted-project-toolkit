# M2 Phase L — Chat fortification round 2 (resume / session picker / thinking)

> **상위**: [`00_master_plan.md`](00_master_plan.md) · [`m2_phase_g.md`](m2_phase_g.md) (G 가 1차 라운드, 이번이 2차)
>
> Status: done (2026-06-01)
> Estimated: 1 작업일 / 2~3 PR
> Depends on: Phase G (manifest picker), Phase I (session_events 영속화), Phase J (archive viewer)
> Blocks: 없음

## 목적 (한 줄)

geny-executor 가 *이미 제공하는* 멀티-턴 메모리 / 세션-스코프 hook /
thinking budget 표면을 **제대로 활용**해, 같은 세션 안에서 어시가 이전 turn 을
기억하고, archive 된 세션을 이어서 진행하고, 모델/think 를 패널에서 직접
조작할 수 있게 한다.

---

## 왜 지금 (분석)

오늘의 채팅이 *완전한 망각형* 임을 코드 추적으로 확인 (2026-06-01):

```python
# server/src/gapt_server/agent/session_registry.py:_drive_pipeline
async for ev in runtime.pipeline.run_stream(message):  # ← state 인자 없음
```

[`geny_executor/core/pipeline.py:1278`](../../../geny-executor/src/geny_executor/core/pipeline.py#L1278)
`_init_state(state=None)` 가 `PipelineState()` *새로* 만듦 →
`state.messages = []` → 매 turn 이 1번째 turn 인 줄 안다.

### geny-executor 가 *이미* 제공하는 것

| 표면 | 어디 | 우리 사용 여부 |
|---|---|---|
| `state` 인자 in-place 보존 | `Pipeline.run_stream(input, state=...)` | ❌ 안 씀 |
| `state.session_id` (caller 가 set, 자동 생성 X) | `PipelineState.session_id` | ❌ 안 채움 |
| `state.messages` (Anthropic 포맷, stage 들이 append) | `PipelineState.messages` | ❌ 안 활용 |
| `state.session_runtime` (free-form plugin slot) | `_init_state` | ❌ 안 씀 |
| 9개 hook (SESSION_START/END / USER_PROMPT_SUBMIT / LOOP_ITERATION_END 등) | `geny_executor/hooks/events.py` | △ PRE/POST_TOOL_USE 만 씀 |
| `ModelConfig.thinking_budget_tokens / thinking_enabled / thinking_type` | manifest 의 api stage config | ❌ UI 노출 X |
| Persist stage (s20) + `restore_state_from_checkpoint` | `s20_persist/restore.py` | ❌ 안 씀 |

→ **conversation memory 가 깨진 게 아니라 우리가 안 쓴 것**. 이 cycle 의 핵심.

### 사용자 요구 ↔ executor 표면 매핑

| 사용자 요구 (이미지의 vscode plugin 류) | executor 표면 | GAPT 가 해야 할 일 |
|---|---|---|
| 저장된 세션을 불러와 이어서 대화 | `run_stream(message, state=state_with_messages)` | runtime 이 PipelineState 보존, rehydrate 시 messages 재구성 |
| 패널에서 모델 / think 조작 | `ModelConfig.thinking_*` | manifest override 에 thinking_* 추가, ThinkingPill UI |
| 세션 선택 dropdown | `listSessions(project_id)` 기존 | UI 만 + workspace 필터 |
| Archived 세션 활성화 | DB status 컬럼만 | 단순 status flip endpoint |

---

## 진입 조건

- [x] Phase G 완료 — manifest picker / 모델 override 인프라
- [x] Phase I 완료 — session_events 영속화 (memory 재구성 source)
- [x] Phase J 완료 — `build_transcript` (turn grouping helper, messages 재구성 재사용 가능)
- [x] 사용자 confirm: L.1→L.5 전부 (2026-06-01)

## DoD (Phase L 종료 게이트)

- [ ] 같은 세션 내 turn 2 가 turn 1 의 사용자 입력을 기억함
      (검증: "내 이름은 alice" → "내 이름?" → 어시가 "alice" 라고 답)
- [ ] ChatPanel 헤더에 세션 picker dropdown — 워크스페이스의 active +
      archived 세션 모두 나옴, 클릭하면 그 세션으로 switch (events replay)
- [ ] Archived 세션을 picker / SessionDetail 에서 "이어서" 버튼 누르면
      active 로 reactivate, ChatPanel 이 attach
- [ ] ChatPanel 헤더에 ThinkingPill — 현재 thinking budget 표시, 클릭하면
      preset (off / 1k / 4k / 16k) 또는 custom 입력으로 변경
- [ ] 세션 생성 시 thinking budget override 가 manifest 의 api stage config 로 전달
- [ ] 회귀 안 됨 — 기존 단일 turn 채팅 / cost / archive / transcript 모두 정상

---

## 작업 항목

### L.1 — Multi-turn 메모리 via `state.messages`

**핵심 변경**: `SessionRuntime` 가 `conversation_state: PipelineState | None`
field 추가. `_drive_pipeline` 가 매 turn 마다 같은 state 객체를 `run_stream` 에
전달. executor 가 in-place mutate → 다음 turn 도 같은 state 사용.

**구체 구현**:

```python
# session_registry.py
@dataclass
class SessionRuntime:
    ...
    # geny-executor 의 PipelineState 보존 인스턴스. None 이면
    # _drive_pipeline 이 lazy 로 첫 turn 직전에 생성한다.
    conversation_state: PipelineState | None = None

async def _drive_pipeline(runtime: SessionRuntime, message: str) -> None:
    if runtime.conversation_state is None:
        runtime.conversation_state = PipelineState(
            session_id=runtime.session_id,  # ← executor SESSION_*  hook 가 묶임
        )
    state = runtime.conversation_state
    async for ev in runtime.pipeline.run_stream(message, state=state):
        ...  # 기존과 동일
    # state 가 in-place mutate 됨 — runtime 이 같은 참조를 들고 있으니
    # 다음 invoke 가 알아서 이전 messages 를 본다.
```

**Rehydrate 경로** (`_runtime_or_rehydrate`):
- 기존 session 을 rehydrate 할 때 session_events 에서 messages 재구성
- Phase J 의 `build_transcript` 재사용 → Turn 리스트 → flat-map 으로
  `[{role:user, content:turn.user}, {role:assistant, content:turn.assistant}, ...]`
- 빈 user / 빈 assistant 는 skip (legacy 세션 호환)
- 빌더는 `agent/transcript.py` 옆에 `to_anthropic_messages(transcript)` 신규 helper
- rehydrate 한 messages 로 `PipelineState(session_id=..., messages=msgs)` 만들어
  `runtime.conversation_state` 에 박음

**한계 (이번 cycle 안 함, 별도 cycle 후보)**:
- Tool-call history 재구성 (tool_use / tool_result content block) — 텍스트만 복원
- Mid-turn snapshot 으로 다시 시작 — 무조건 turn 경계에서만 resume

**Tests**:
- `tests/agent/test_multi_turn_memory.py` 신규
  - fake pipeline 으로 1번 invoke → state.messages 에 user/assistant 들어감 확인
  - 2번째 invoke 가 같은 state 받는지 확인
  - rehydrate 시 session_events 로부터 messages 복원 확인

---

### L.2 — Reactivate archived sessions

**Backend**:
- `POST /_gapt/api/sessions/{session_id}/reactivate` 신규 endpoint
- `session_manager.reactivate(session_id, user)` — status `ARCHIVED → ACTIVE`,
  `last_active_at = now()`. Audit log: `session.reactivate`
- 이미 active 면 idempotent (200 + 동일 응답)
- 다른 active 세션이 같은 workspace 에 있어도 막지 않음 (의도: 한 워크스페이스에
  여러 active 가능, picker 가 선택)

**Frontend**:
- `web/src/api/sessions.ts` — `reactivateSession(id)` 함수
- SessionDetail 헤더의 "워크스페이스 열기" 옆 / 또는 아래에
  "이어서 진행 →" 버튼 (archived 일 때만 노출).
  클릭 → reactivate API → workspace 라우트로 이동하면서 query string 으로
  `?session=<id>` 힌트 (L.3 의 SessionPicker 가 이 hint 로 auto-select)

---

### L.3 — Session picker in ChatPanel header

**현재**: ChatPanel mount 시 `listSessions(projectId)` →
`workspace_id === wid && status === "active"` 첫 row 자동 attach. 다른 세션
못 봄.

**변경**:
- 헤더의 ManifestPill / ModelPill 옆에 `SessionPicker` 컴포넌트
- 드롭다운 내용:
  - "현재 세션" 표시 (활성 세션 ID + first_user_message snippet)
  - "이 워크스페이스의 다른 세션" — workspace_id 필터한 list
  - 각 row: status badge, first_user_message snippet, turn count, cost, age
  - active 클릭 → switch (events replay)
  - archived 클릭 → "이어서 진행" 버튼 (L.2 의 reactivate 호출 후 switch)
- "+ 새 세션" 옵션 — 기존 "Start session" UI 와 통합 가능
- URL 의 `?session=<id>` 쿼리 파라미터로 진입 시 자동 select (deep link)

**Backend 보강**:
- `listSessions` 에 `?workspace_id=<wid>` 쿼리 옵션 추가 (지금은 project_id 만)
- 응답 정렬: `last_active_at DESC` (active 가 위로)

---

### L.4 — ThinkingPill + thinking budget per-session override

**Backend**:
- `Settings` / `admin_agent_prefs` / `CreateSessionInput` 의 override 필드에
  `thinking_budget_tokens: int | None` + `thinking_enabled: bool | None` 추가
- `session_manager._merge_overrides` 가 이 두 필드도 manifest 의 stage 6
  (api) config 에 patch
- 기본값: manifest 가 `"thinking_enabled": false` (이전과 동일 — 명시 안 한
  manifest 는 변화 없음)

**Frontend**:
- `web/src/chat/ThinkingPill.tsx` 신규
  - 표시: budget=0 / off 일 때 "think: off", 값 있을 때 "think: 4k" 형식
  - 클릭 → 작은 popup: preset 3개 (off / 1k / 4k / 16k) + custom 입력
  - 변경값은 ModelPill 과 동일하게 localStorage 에 sticky (per-project)
- `CreateSessionInput` 에 두 필드 추가 (`web/src/api/sessions.ts`)
- ChatPanel 의 "Start session" 시 ThinkingPill 의 현재값을 함께 전달
- 세션 active 중에는 disabled (manifest commit 됐으므로) — ModelPill 과 동일 패턴

**i18n**:
- `chat.thinking.label` / `chat.thinking.off` / `chat.thinking.custom` 등 키

---

### L.5 — Tests + live smoke + drift

**Tests**:
- `tests/agent/test_multi_turn_memory.py` (위 L.1)
- `tests/sessions/test_routes.py` 확장 — `test_reactivate_session` (archive →
  reactivate → status=active + audit)
- `tests/sessions/test_routes.py` 확장 — `listSessions?workspace_id=` 필터 동작

**Live smoke**:
1. "내 이름은 alice 야" → "내 이름이 뭐였지?" → 어시가 "alice" 답함 (L.1)
2. 같은 워크스페이스에 두 번째 세션 만들기 → 헤더 picker 에 두 세션 보임,
   switch 가능 (L.3)
3. 세션 1 archive → picker 에 표시 → "이어서" → reactivate + 메모리 유지 (L.2)
4. ThinkingPill 4k 로 설정 후 새 세션 → api.request 이벤트의 model_config 에
   `thinking_budget_tokens=4096` (L.4)

**Drift + memory**:
- progress card 마감, 발견된 패턴 (예: "PipelineState 는 caller 가 박는다") 메모리 추가

---

## 산출물 요약

```
server/
  src/gapt_server/agent/session_registry.py            (SessionRuntime.conversation_state + _drive_pipeline)
  src/gapt_server/agent/transcript.py                  (to_anthropic_messages helper)
  src/gapt_server/agent/session_manager.py             (reactivate + _merge_overrides 확장)
  src/gapt_server/routers/sessions.py                  (reactivate endpoint + listSessions workspace 필터 + _runtime_or_rehydrate 가 messages 복원)
  tests/agent/test_multi_turn_memory.py                (신규)
  tests/sessions/test_routes.py                        (reactivate + workspace 필터 확장)

web/
  src/api/sessions.ts                                  (reactivateSession + workspace 필터 옵션 + ThinkingOverride 타입)
  src/chat/SessionPicker.tsx                           (신규)
  src/chat/ThinkingPill.tsx                            (신규)
  src/chat/ChatPanel.tsx                               (헤더 + state 흐름 통합)
  src/routes/SessionDetail.tsx                         ("이어서 진행" 버튼)
  src/i18n/en.ts / ko.ts                               (keys)

docs/
  plan/m2_phase_l.md                                   (이 파일)
  plan/00_master_plan.md                               (index)
  progress/m2_phase_l.md                               (신규)
```

---

## 검증 시나리오 (자세히)

1. **Multi-turn 메모리**
   - new session 만들고 "내 이름은 alice" 입력 → 어시 응답.
   - 같은 세션에 "내 이름이 뭐였지?" 입력 → 어시가 "alice" 답.
   - 검증: 첫 turn 의 user_message + text event 가 session_events 에 들어가있고,
     두 번째 invoke 직전 runtime.conversation_state.messages 에 4개 entry
     (user, assistant, user, ...) 가 있음 (test).

2. **Session picker**
   - 워크스페이스에 2개 세션 있는 상태 → picker dropdown 에 2개 row.
   - 두 번째 클릭 → ChatPanel 이 그 세션의 events replay 함, 헤더 정보 갱신.

3. **Reactivate**
   - 세션 1 을 SessionDetail 에서 archive (기존 archive 동작 우선 추가 필요시).
   - picker 에 "archived" tone 으로 표시.
   - "이어서" 클릭 → reactivate → 세션이 active 로 돌아오고 ChatPanel 이 그
     세션으로 attach. 이전 messages 유지 (L.1 의 rehydrate 가 동작).

4. **ThinkingPill**
   - ThinkingPill "4k" 선택 후 "Start session" → CreateSessionInput 에
     thinking_budget_tokens=4096 전달.
   - api.request 이벤트의 model_config / api stage config 확인.

---

## 리스크 + 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| `state` 를 reuse 하면 stage 내부 변수가 오염되어 회귀 | 다른 stage 가 깨질 수 있음 | executor 의 `_init_state` 가 fresh fields 만 채움 (line 1284-1291) — messages 외에는 새 reset. 단위 테스트로 회귀 감지 |
| Tool-call history 재구성 안 함 → 어시가 "내가 무슨 도구 썼지?" 못 답함 | 가독성 ↓, 그러나 한 turn 안에서는 정상 | Plan 의 한계로 명시. tool_use/tool_result content block 복원은 별도 cycle |
| Reactivate 가 같은 workspace 에 active 세션 여러 개 만듦 | picker 가 어수선 | picker 정렬: last_active_at DESC. 의도: 사용자가 관리 |
| ThinkingPill 의 thinking_enabled=true 가 일부 모델에서 미지원 | API 에러 | manifest 의 model 이 thinking 지원 모델일 때만 활성화 (sonnet, opus). gpt-4o / gemini 는 pill disabled |
| Rehydrate 시 session_events 1000+ 건 → messages 1000+ entry → context window 초과 | API 에러 | message 개수 cap (예: 마지막 50개) + summary placeholder. Cycle 내 구현 |

---

## Out of scope (이번 cycle 안 함)

- SESSION_START / SESSION_END / USER_PROMPT_SUBMIT hook 도입
  (Phase I.2 의 USER_MESSAGE publish 를 hook 으로 이전) — 별도 cycle
- Tool-call history 재구성 — 별도 cycle
- Persist stage (s20) checkpoint 활용 — DB 의 session_events 가 같은 역할
- Mid-turn snapshot 으로 재시작
- Claude CLI 의 native `--resume` 매핑 (geny-executor 가 안 노출)
- Multi-agent / subagent
- 세션 간 messages 합치기 (다른 세션의 메모리 이어 받기)

---

## 관련 docs / 메모리

- [`m2_phase_g.md`](m2_phase_g.md) — manifest picker / 모델 override
- [`m2_phase_i.md`](m2_phase_i.md) — session_events 영속화 / transcript export
- [`m2_phase_j.md`](m2_phase_j.md) — `build_transcript` (메모리 재구성 재사용)
- [[reference_geny_executor_v2_1]] — 21단계 / hook / mutator 표면
- [[feedback_extend_executor_not_adapter_layer]] — pipeline 설계는 executor 가
  담당, GAPT 는 *제대로 사용하는 로직* 만 담당 (이 plan 의 출발 원칙)
