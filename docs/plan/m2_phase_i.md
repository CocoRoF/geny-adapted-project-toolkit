# M2 Phase I — 세션 기록 무결성 (Session recording integrity)

> **상위**: [`00_master_plan.md`](00_master_plan.md) · [`m2_m5_outline.md`](m2_m5_outline.md)
>
> Status: done (2026-06-01)
> Estimated: 1 작업일 / 2–3 PR
> Depends on: Phase D.3 (session_events 영속화), Phase G (manifest picker)
> Blocks: 없음 — v1 종료 게이트 외부 robustness cycle

## 목적 (한 줄)

세션의 **모든 turn 을 누락 없이** DB 에 기록한다 — 사용자 입력 / 어시스턴트
응답 / 도구 호출·결과 / 비용. 추후 운영자가 "어떻게 vibe-코딩 했는지"
를 재구성할 수 있는 **archive 품질** 보장.

---

## 왜 지금 (근거 데이터)

`agent_sessions` 라이브 row 검증 (2026-06-01):

```
id                        | manifest      | cost_usd | in_tokens | out_tokens
01KSS1QBY520BX77BT4B2CMW7S | gapt_default | 0.000000 | 0         | 0
01KSQ7MH2GV9R0497YVWCE19BE | gapt_default | 0.000000 | 0         | 0
```

비용 대시보드 → `$0.0000`, 두 세션 다 0 토큰. 그런데 `session_events`
로그를 직접 보면:

```
seq=49 kind=done data={"cost": {"input_tokens":6, "output_tokens":42,
  "cost_usd":0.0, "tool_calls":0, ...}}
```

→ 토큰은 실제로 추적되고 있는데 **DB column 으로 안 옮겨감**. 게다가
`cost_usd:0.0` 이다 토큰이 있는데도.

### 세 가지 잠복 버그 확인

**1. Cost callback 미연결 (확실)** — [`agent/session_registry.py:227-232`](../../server/src/gapt_server/agent/session_registry.py#L227)
```python
if event_type == "token.tracked":
    _update_accumulator(runtime, data)   # ← in-memory accumulator update
    await runtime.bus.publish(
        SessionEventKind.COST, runtime.accumulator.snapshot()
    )
    continue                             # ← DB write 콜백 호출 안 됨
```

DB write 로직 (`_on_cost_update` in [`routers/sessions.py:248-269`](../../server/src/gapt_server/routers/sessions.py#L248))
은 `cost_hook.build_cost_hook` 의 POST_TOOL_USE 핸들러에서만 호출됨.
**도구를 안 쓰는 채팅 세션** (예: "안녕" 같은 단순 질문) 은 POST_TOOL_USE
가 안 발생 → DB 영원히 0.

**2. 사용자 입력 미기록 (vibe-archive 핵심 누락)** —
`SessionEventKind` 에 `USER_MESSAGE` 가 없음. `POST /sessions/{sid}/invoke`
가 `runtime.invoke(message)` 호출 → executor 응답만 publish.
**user message 가 session_events 테이블 어디에도 없음** → archive 가 한쪽 (assistant) 만.

**3. `cost_usd` 자체가 0** — geny-executor `s07_token` 의
`AnthropicPricingCalculator._get_prices(model)`:
- exact match: `pricing[model]` — manifest 는 `"model": "sonnet"`, dict 는 `"claude-sonnet-4-6"` 등 full ID → miss
- prefix match: `"sonnet".startswith("claude-sonnet-4")` → False → miss
- → `return 0.0`

CLI 가 `sonnet` 을 `claude-sonnet-4-6` 로 resolve 하더라도 executor 가
모르고 manifest 의 string 그대로 사용. **alias resolution gap**.

---

## 진입 조건

- [x] Phase D.3 영속화 (session_events 테이블) 작동 중
- [x] Phase G manifest picker 작동 중 (manifest 별 model string 확인 가능)
- [x] 사용자 confirm: I.1→I.4 전부 (2026-06-01)

## DoD (Phase I 완료 게이트)

- [ ] 도구 없는 단순 채팅 1 turn 후 `agent_sessions.cost_usd > 0` (model=sonnet 기준)
- [ ] `session_events` 에 사용자 prompt 가 `user_message` kind 로 1 row 이상
- [ ] `gapt_default` (sonnet) / `gapt_anthropic_sdk` (sonnet) / `gapt_openai` (gpt-4o) /
      `gapt_google` (gemini-2.5-flash) 4개 manifest 모두에서 cost 가 0 이 아님 (각 1 turn 검증)
- [ ] `GET /_gapt/api/sessions/{id}/transcript?format=markdown` 가 user / assistant /
      tool 단위로 정렬된 텍스트 반환
- [ ] Chat panel header 에서 "transcript 다운로드" 클릭 가능

---

## 작업 항목

### I.1 — Cost callback 을 `token.tracked` 경로에서도 호출

**현재**: `_drive_pipeline` 의 token.tracked 처리 → in-memory accumulator 만 업데이트.
**수정**:
- `SessionRuntime` 에 `cost_callback: Callable[[CostAccumulator], Awaitable[None]] | None` field 추가
- `_build_runtime_from_handle` 에서 `runtime.cost_callback = _on_cost_update`
- `_drive_pipeline` 의 token.tracked 분기에서:
  - `_update_accumulator(runtime, data)`
  - `if runtime.cost_callback: await runtime.cost_callback(runtime.accumulator)`
  - 기존 `runtime.bus.publish(COST, ...)` 라인은 제거 (cost_callback 이 publish 도 책임)

**산출물**:
- `server/src/gapt_server/agent/session_registry.py` 수정
- `server/src/gapt_server/routers/sessions.py` — `_build_runtime_from_handle` 에서 runtime.cost_callback 세팅
- `server/tests/agent/test_cost_db_sync.py` — 신규: token.tracked 발생 시 cost_callback 호출 + AgentSession row 업데이트 round-trip

**리스크**: 기존 POST_TOOL_USE 경로의 `on_cost_update` 호출과 중복 발생 가능
→ accumulator 가 delta 가 아닌 absolute snapshot 을 들고 있어서 멱등.
   `_last` 캐시가 d_in/d_out/d_cost = 0 이면 DB write 스킵.

---

### I.2 — `USER_MESSAGE` event kind + 사용자 입력 publish

**현재**: `runtime.invoke(message)` → `_run_with_lifecycle(runtime, message, runner)` →
runner 만 호출. message 자체는 publish 안 됨.

**수정**:
- `SessionEventKind.USER_MESSAGE = "user_message"` 추가
- `_run_with_lifecycle` 의 try 진입 *최초* 라인에 `await runtime.bus.publish(USER_MESSAGE, {"text": message})`
- 자동으로 persister 가 session_events 에 기록
- Web SSE 핸들러 (`web/src/api/sessions.ts`) — `user_message` kind 인식 (기존 user-side bubble UI 가 SSE 로 늦게 도착해도 OK)

**산출물**:
- `server/src/gapt_server/agent/streaming.py` (enum 추가)
- `server/src/gapt_server/agent/session_registry.py` (publish)
- `web/src/api/sessions.ts` (TS enum 추가)
- `web/src/chat/ChatPanel.tsx` (user bubble 가 user_message replay 로도 동작하는지 확인)
- `server/tests/agent/test_user_message_persist.py` — invoke 후 session_events 에 user_message row 존재 확인

**왜 publish 가 invoke() 가 아닌 _run_with_lifecycle**:
invoke() 안에서 publish 하면 task spawn 전이라 bus subscriber 가 아직 attach 안 됐을 수 있음. lifecycle 의 첫 줄이 가장 안전 (task context 안, bus 구독자 attach 완료 후).

---

### I.3 — Model-alias fallback pricing

**현재**: manifest 의 `"model": "sonnet"` → upstream pricing dict miss → cost 0.

**해결책 (이중 layer)**:

(a) **GAPT-side fallback pricing module** —
   `server/src/gapt_server/agent/pricing.py` 신규.
   - GAPT 가 직접 관리하는 model → price 매핑 (alias 포함)
   - `resolve_pricing(model: str) -> dict | None` — alias / prefix / canonical 순으로 검색
   - alias 테이블: `sonnet → claude-sonnet-4-6`, `haiku → claude-haiku-4-5-20251001`,
     `opus → claude-opus-4-6`, `gpt-4o → gpt-4o`, `gemini-2.5-flash → gemini-2.5-flash` 등
   - upstream `geny_executor.stages.s07_token.pricing.ALL_PRICING` import 해서 base 로 사용,
     alias 만 GAPT 가 add (upstream drift 가 적음)

(b) **`_update_accumulator` 에서 fallback 적용** —
   - `data["cost_usd"]` 가 0 이고 input_tokens > 0 이면 GAPT pricing 으로 재계산
   - resolve model: api 스테이지 config 에서 model string 꺼냄 (manifest 의 `stages[5].config.model` 또는
     `runtime.pipeline` 의 컨텍스트). 없으면 fallback "claude-sonnet-4-6"
   - 결과 cost 를 `data["cost_usd"]` 에 채워서 기존 accumulator update 흐름 유지

**산출물**:
- `server/src/gapt_server/agent/pricing.py` 신규
- `server/src/gapt_server/agent/session_registry.py` — `_update_accumulator` 확장
- `server/tests/agent/test_pricing_fallback.py` — 단위 (alias / prefix / unknown)
- `server/tests/agent/test_token_tracked_fallback.py` — _update_accumulator 통합 (cost=0 + tokens > 0 → fallback fires)

**왜 upstream 도 별도로**: geny-executor 의 `ALL_PRICING` 에도 sonnet/haiku/opus 별칭 PR 이 맞지만
이번 cycle 의 작업 단위에는 안 포함. 메모리에 노트 남기고 별도 cycle (또는 별도 의존성 bump) 로.

---

### I.4 — Transcript export 엔드포인트 + 다운로드 UI

**현재**: `/messages?since=N` 로 SSE 이벤트 replay 만. 운영자가 "이 세션의 대화를 한 번에 보기" 어려움.

**수정**:
- `GET /_gapt/api/sessions/{sid}/transcript?format={json,markdown}` 신규
- 백엔드:
  - session_events 전부 읽음, ts 순 정렬
  - turn 단위로 group: `user_message` 이 새 turn 시작 → 그 안의 `text` / `tool_call` / `tool_result` / `cost` 묶음
  - JSON: `{turns: [{user: "...", assistant: "...", tool_uses: [{name, input, output}], cost: 0.001}]}`
  - markdown: `### Turn 1\n**User**: ...\n**Assistant**: ...\n#### Tool: bash\n...\n` 형식
- 프론트:
  - `web/src/chat/ChatPanel.tsx` 헤더에 "다운로드" 버튼 — markdown 파일로 저장 (filename: `session-<id>-<date>.md`)
- 인증: 기존 admin auth + project membership

**산출물**:
- `server/src/gapt_server/routers/sessions.py` — endpoint
- `server/src/gapt_server/agent/transcript.py` 신규 — turn grouping + markdown render
- `web/src/api/sessions.ts` — `downloadTranscript(sessionId, format)`
- `web/src/chat/ChatPanel.tsx` — 헤더 버튼
- `server/tests/sessions/test_transcript.py` — JSON / markdown 형식 round-trip

---

## 산출물 요약

```
server/
  src/gapt_server/agent/streaming.py                 (USER_MESSAGE 추가)
  src/gapt_server/agent/session_registry.py          (cost_callback + publish user_message)
  src/gapt_server/agent/pricing.py                   (신규 — fallback pricing)
  src/gapt_server/agent/transcript.py                (신규 — turn grouping + md render)
  src/gapt_server/routers/sessions.py                (runtime.cost_callback 세팅 + /transcript endpoint)
  tests/agent/test_cost_db_sync.py                   (신규)
  tests/agent/test_user_message_persist.py           (신규)
  tests/agent/test_pricing_fallback.py               (신규)
  tests/agent/test_token_tracked_fallback.py         (신규)
  tests/sessions/test_transcript.py                  (신규)

web/
  src/api/sessions.ts                                (USER_MESSAGE kind + downloadTranscript)
  src/chat/ChatPanel.tsx                             (transcript 다운로드 버튼)
  src/i18n/en.ts / ko.ts                             (몇 개 키)

docs/
  plan/m2_phase_i.md                                 (이 파일)
  plan/00_master_plan.md                             (인덱스 행)
  progress/m2_phase_i.md                             (신규)
```

---

## 검증 시나리오

1. **Cost DB sync** — 새 세션 만들고 "1+1?" 1 turn → done 후
   `agent_sessions.cost_usd > 0` AND `input_tokens > 0` 확인. 비용 대시보드
   새로고침 → 0 이 아닌 값 표시.
2. **User message log** — 같은 세션의 session_events 조회 → `kind='user_message'`
   행 1개 + `data->>'text' = '1+1?'`.
3. **Alias fallback** — 의도적으로 manifest model 을 `"sonnet"` 으로 두고 turn 진행 →
   token.tracked event 의 cost_usd 가 (0이 아닌 GAPT fallback 가격) 으로 채워짐.
4. **Transcript JSON** — `curl /sessions/{id}/transcript?format=json` →
   `{turns: [{user: "1+1?", assistant: "2", ...}]}`.
5. **Transcript markdown 다운로드** — ChatPanel 헤더 "다운로드" 클릭 →
   브라우저가 `.md` 파일 저장. 내용 정상.

---

## 리스크 + 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| cost_callback 중복 호출 (token.tracked + POST_TOOL_USE) | DB write 2배 | `_last` 캐시 + delta=0 skip 로 멱등. metric inc 도 0 skip. |
| 영구 publish 한 user_message 가 turn 중간에 끼면 replay 순서 깨짐 | 채팅 UI 헝클어짐 | `_run_with_lifecycle` *최초* 라인이라 seq=0 보장; 다음 user invoke 이전 모든 이벤트 이후 |
| transcript 가 큰 세션에서 메모리 폭발 | 응답 지연 / OOM | 페이지네이션 없음 (single-admin 가정, 1세션 < 10MB) — 1MB 넘어가면 warning 로그 |
| upstream geny-executor 의 pricing 이 바뀌면 GAPT fallback 과 drift | 비용 계산 차이 | GAPT pricing 은 alias 위주, base 는 upstream import — drift 영향 최소화. CI 단위 테스트로 detect. |
| 사용자가 transcript 를 외부 공유 시 secret 노출 가능 | 보안 | 1차 범위 밖. tool_result 안의 env 값 등은 그대로. "외부 공유 전 수동 검토" warning 추가 |

---

## Out of scope

- session_events 자동 sweep / 보존 정책 — Phase D.3 의 sweep 미구현 그대로 유지 (별도 cycle)
- transcript export 의 PDF / HTML — markdown 충분
- 사용자별 transcript 공유 권한 — single-admin v1 가정
- upstream geny-executor 의 model alias PR — 별도 cycle (또는 dependency bump)

---

## 관련 docs / 메모리

- [[reference_geny_executor_v2_1]] — token 스테이지가 cost 산출
- [[feedback_extend_executor_not_adapter_layer]] — pricing 도 executor 가 source-of-truth 지만,
  GAPT 가 *alias mapping 만* 추가하는 것은 어댑터 다층화 아님 (canonical map 은 그대로 upstream)
- [`m2_phase_g.md`](m2_phase_g.md) — manifest picker (manifest model 값이 어디서 오는지)
- [`docs/09_security_authz_observability.md`](../09_security_authz_observability.md) — 비용 집계 정책
