# M2 Phase M — Post-L deep-review remediation (memory bounds + tech debt)

> **상위**: [`00_master_plan.md`](00_master_plan.md) · [`m2_phase_l.md`](m2_phase_l.md)
>
> Status: in_progress (started 2026-06-01)
> Estimated: 2–3 작업일 / 9 PR (sub-phases)
> Depends on: Phase L (multi-turn / picker / SSE keep-alive + fix-ups)
> Blocks: 없음

## 목적 (한 줄)

Phase L 종료 시점 deep-review 에서 식별된 production risk (P0 — 메모리 무제한)
와 누적 robustness/tech debt (P1~P3) 를 **하나씩 cycle 로 끊어** 해결한다.
P0 limits 는 모두 `Settings` 에서 조정 가능 — 운영자가 환경별로 튜닝.

---

## 왜 지금

Phase L 직후 사용자 요청으로 5 cycle 의 모든 deliverable 을 deep-review:
[`docs/progress/m2_phase_l.md`](../progress/m2_phase_l.md) 의 deep-review 결과
4개 우선순위 그룹 (총 20+ 항목) 식별. 사용자 직접 지시 (2026-06-01):

> "🔴 P0 — production risk 이것을 제대로 해결해야 하고 저런 값들을 config (설정)
> 에서 정확하게 컨트롤 할 수 있게 만들어야만 함. 나머지는 권장되는 설정으로
> 전부 완벽하게 진행하자. 하나하나 완벽하게 진행하고, 진행 시점마다 나에게
> 정확하게 보고해 줘."

---

## DoD

- [ ] P0 3개 항목 (rehydrate cap / runtime LRU+idle eviction / messages cap) 모두
      `Settings` 에서 조정 가능 + 권장 default 적용
- [ ] P1 5개 항목 (override revert / 충돌 / private attr / 누락 테스트 / SSE route test)
      해결
- [ ] P2 4개 항목 (boilerplate / file split / i18n / env UX) 정리
- [ ] P3 5개 항목 (markdown links / syntax highlight / picker overflow / cache tooltip / test env)
      polish 완료
- [ ] 아키 3개 항목 (persist stage / hooks / tool-call history) 결정 + 적용
- [ ] 매 sub-phase 마다 user-facing 보고 (이 plan card 에 sub-phase status update)
- [ ] 전체 회귀 통과: 138/138 server + tsc clean + lint 새 파일 0 error

---

## Sub-phases

진행 status (cycle 진행에 따라 갱신):

| ID | 범위 | Status |
|---|---|---|
| **M.1** | P0: Memory bounds via `Settings` (rehydrate LIMIT + runtime LRU/idle eviction + state.messages cap) | pending |
| **M.2** | P1: Override revert semantics + 핵심 누락 테스트 3건 | pending |
| **M.3** | P1: SSE route test via uvicorn fixture (un-skip) | pending |
| **M.4** | P2: `sessions.py` 분리 + `_runtime_or_rehydrate` Depends 추출 | pending |
| **M.5** | P2: i18n consistency + env target_config edit UX | pending |
| **M.6** | P3: markdown 외부 링크 target=_blank + cache token tooltip + SessionPicker overflow | pending |
| **M.7** | Tool-call history rehydration (Anthropic content blocks) | pending |
| **M.8** | SESSION_START/END/USER_PROMPT_SUBMIT hook 도입 + persist stage 결정 | pending |
| **M.9** | 코드 블록 syntax highlight + `/var/lib/gapt-bare` 권한 정리 | pending |

---

## M.1 — Memory bounds via Settings (P0)

### Settings 신규 키 (권장 default 표시)

| 키 | Default | 의미 |
|---|---|---|
| `session_runtime_cache_size` | 50 | `SessionRegistry` 가 유지하는 max runtime 개수. LRU evict. |
| `session_runtime_idle_eviction_s` | 1800 (30분) | 마지막 SSE/invoke 활동 후 idle 이면 자동 aclose + evict. |
| `session_max_rehydrate_events` | 1000 | `_runtime_or_rehydrate` 의 session_events DB 쿼리 LIMIT (가장 최근 N 개). |
| `session_max_messages_in_state` | 50 (= 25 turn 쌍) | `state.messages` array cap — invoke 직전 trim. |
| `session_max_stream_replay_events` | 2000 | `_full_replay` 의 DB 쿼리 LIMIT. |

### 구현 요약

1. **Settings 확장** — `server/src/gapt_server/settings.py` 5개 필드 추가.
2. **SessionRegistry LRU + idle**:
   - 내부 dict 를 OrderedDict 로 변경, get 시 move_to_end.
   - `register` 시 size 초과면 oldest aclose + pop.
   - 백그라운드 task: 1분 주기 idle check, `last_activity_at` 비교.
3. **Rehydrate LIMIT** — `_runtime_or_rehydrate` 의 event_rows 쿼리에
   `ORDER BY seq DESC LIMIT N` 적용, 그 후 ASC re-sort.
   `to_anthropic_messages(max_turns=...)` 도 settings 로 통일.
4. **state.messages cap** — `_drive_pipeline` 직전 trim:
   `if len(state.messages) > 2*max_turns: state.messages = state.messages[-2*max_turns:]`
   (user+assistant 쌍 단위 cap).
5. **_full_replay LIMIT** — DB 쿼리에 LIMIT 적용. 초과 시 (older 잘림) 로그 1회.

### 테스트
- `test_registry_lru_evicts_oldest` — size=2 에서 3개 register, oldest evict 확인.
- `test_registry_idle_eviction` — fake clock 으로 idle 시간 흐름 후 evict 확인.
- `test_rehydrate_caps_events` — DB 에 1000+ events 시 (모킹) LIMIT 적용 + messages cap.
- `test_full_replay_respects_limit` — settings cap 적용 round-trip.

### 라이브 검증
- 라이브 세션 (250 events) 정상 작동 확인.
- 일부러 long-running 시뮬레이션 (fake event seeder) → cap 적용 + memory 안정.

---

## M.2 — Override revert semantics + 핵심 테스트 (P1)

### Override "inherit 복귀" 지원
- `InvokeRequest` 에 `clear_overrides: list[str]` 필드 추가 (예: `["model", "thinking"]`).
- 백엔드: clear_overrides 에 "model" 있으면 `runtime.conversation_state.model = original_manifest_model` (manifest 기본값으로 복귀).
- 프론트: ModelPill / ThinkingPill 의 "inherit" 선택 시 `clear_overrides` 에 추가.
- Alternative cleaner: 별도 endpoint `POST /sessions/{id}/clear-overrides`.

### 누락 테스트 3건 추가
1. `test_invoke_model_override_mutates_state` — model="opus" 보내고 다음 invoke 의 state.model 확인.
2. `test_full_replay_combines_bus_and_db` — bus 에 5 events / DB 에 추가 10 events 시 _full_replay(since=0) 가 15 events 반환 + 정렬 + 중복 없음.
3. `test_reactivate_rehydrate_round_trip` — archive → reactivate → invoke → 이전 turn 의 메모리 보존 확인 (end-to-end).

---

## M.3 — SSE route test via uvicorn fixture (P1)

- 현재 `test_stream_emits_text_and_done` 가 ASGITransport 버퍼링 한계로 skip.
- pytest fixture: 동적 포트로 uvicorn 띄움 (서브프로세스), 실제 TCP socket 통해 SSE 검증.
- 후속 SSE 테스트 (turn 2 keep-alive, prefix_events 흐름, 재연결) 추가 가능.

---

## M.4 — sessions.py 분리 + Depends 추출 (P2)

### 분리
`server/src/gapt_server/routers/sessions.py` (1000+ LOC) →
- `sessions/crud.py` — create / list / get / archive / reactivate
- `sessions/invoke.py` — invoke / interrupt / stream / messages / transcript
- `sessions/_shared.py` — `_runtime_or_rehydrate`, `_full_replay`, runtime Depends, error mapping

### Depends 추출
- `get_session_runtime(session_id) -> SessionRuntime` Depends factory.
- 호출자: `runtime: SessionRuntime = Depends(get_session_runtime)` 한 줄.
- 8개 인자 boilerplate 사라짐.

---

## M.5 — i18n + env target_config UX (P2)

### i18n
- `SessionDetail.tsx` / `SessionsHistory.tsx` / `ChatPanel.tsx` 의 하드코딩 ko 텍스트
  → `web/src/i18n/{en,ko}.ts` 의 카탈로그로 이전.
- 새 키 prefix: `chat.picker.*`, `chat.thinking.*`, `session_detail.*`, `sessions_history.*`.

### env target_config edit UX
- Phase H 의 write-time validation 으로 legacy row 의 잘못된 필드 edit 시 422 발생.
- EnvironmentEditor 가 422 의 `fields[]` 를 inline error 로 명확히 표시 (이미 일부 구현, 보강).
- "잘못된 키 자동 정리" suggestion banner — 알려진 unknown key 표시 + "삭제" 1-click.

---

## M.6 — UX polish (P3)

- `MarkdownText` 의 `<a>` renderer override → `target="_blank" rel="noopener noreferrer"`.
- `cache_write/read tokens` 표시에 hover tooltip — "Anthropic 의 prompt caching: 비용 절감용".
- SessionPicker dropdown 의 width / overflow — 워크스페이스 30+ 세션 시에도 깔끔.

---

## M.7 — Tool-call history rehydration

- `to_anthropic_messages` 가 현재 텍스트만. Anthropic API messages 의 `content` 가
  `list[{"type": "tool_use", "id": ..., "name": ..., "input": ...}]` / `tool_result` 도 지원.
- `build_transcript` 의 turn.tool_uses 활용해 messages 의 user/assistant content 를
  text + tool_use/result mixed list 로 빌드.
- 어시가 "내가 turn 3 에서 무슨 도구 썼지?" 답할 수 있어짐.

---

## M.8 — Hook adoption + persist stage 결정

- `SESSION_START` / `SESSION_END` / `USER_PROMPT_SUBMIT` / `LOOP_ITERATION_END` 도입.
- Phase I.2 의 USER_MESSAGE publish 를 USER_PROMPT_SUBMIT hook 으로 통합.
- audit log 와 cost callback 도 session lifecycle hook 으로 일관성 ↑.
- persist stage (s20) checkpoint 사용 여부 결정: GAPT 의 session_events 가 이미 source-of-truth
  이므로 persist stage **비활성** (이중 source 방지).

---

## M.9 — Syntax highlight + bare permission (P3)

- `shiki` (small, themed) 도입 → `MarkdownText` 의 `<pre><code>` 에 highlight.
- `/var/lib/gapt-bare` 권한 오류 (test 환경 / sandbox 권한) → 설정의
  `workspace_bare_root` 가 dev 에서 `${HOME}/.local/share/gapt-bare` 로 fallback (이미
  `scripts/dev/server.sh` 에 export 있음). 테스트 fixture 가 이 env var 존중하도록 정리.

---

## 진행 보고 형식 (cycle 별)

매 sub-phase 종료 시:
1. **변경**: 파일 + 줄번호 + 핵심 패턴
2. **테스트**: 신규 / 회귀 결과 (X/Y pass)
3. **라이브 검증**: 가능한 경우
4. **확장 가능성**: 다음 sub-phase 와의 의존 / 발견된 새 issue
5. plan card 의 sub-phase status 갱신

---

## 관련 docs / 메모리

- [`m2_phase_l.md`](m2_phase_l.md) — deep-review 의 원본 (마지막 절)
- [[reference_geny_executor_v2_1]] — hook / state / stage 표면
- [[feedback_executor_state_passing]] — state 보존 책임 분리 원칙
- [[feedback_extend_executor_not_adapter_layer]] — executor 능력 활용 우선 원칙
