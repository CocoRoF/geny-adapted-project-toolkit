# M2 Phase K — Session archive polish (markdown + cache tokens)

> **상위**: [`00_master_plan.md`](00_master_plan.md) · [`m2_phase_j.md`](m2_phase_j.md)
>
> Status: done (2026-06-01)
> Estimated: 0.5 작업일 / 1–2 PR
> Depends on: Phase I (cost DB sync, transcript), Phase J (archive viewer)
> Blocks: 없음

## 목적 (한 줄)

Phase J 의 viewer 가 "보여주긴 한다" 수준 — 어시 응답이 plain `<pre>` 라
코드블록/리스트가 죽고, 비용 화면이 "6 토큰 / $0.013" 으로 보여서
사용자가 cache_write 토큰의 존재를 모른다. 두 갭을 한 cycle 로 메운다.

---

## 왜 지금

**K.1 — 마크다운 갭**:
- Phase I.4 transcript renderer 는 어시 응답을 `<pre>` 로 dump. 사용자가
  `라이브 ChatPanel` 에서도 같은 처리를 봄.
- 어시가 코드블록 / 리스트 / 인라인 코드를 쓰면 형식 없음 → 가독성 ↓.

**K.2 — cache 토큰 미가시화**:
- 라이브 검증 (2026-06-01, [`docs/progress/m2_phase_i.md`](../progress/m2_phase_i.md))
  의 결과: `agent_sessions.input_tokens=6, output_tokens=6, cost_usd=0.012902`.
- 사용자 입장에서 "6 * ($3/M + $15/M) = $0.000108" 이라 cost 가 *120배 다름*.
- 원인: executor 의 `token.tracked` payload 가 `cache_write` / `cache_read`
  도 포함, GAPT 의 `compute_cost_usd` 가 이들의 가격을 더함. 하지만
  `CostAccumulator` 가 cache 카운트를 미보존 → DB / UI 둘 다 안 보임.
- 이 cycle 에서 **추적 + 표시**까지 완료.

---

## 진입 조건

- [x] Phase J done — SessionsHistory / SessionDetail 라우트 작동
- [x] Phase I done — cost_callback + transcript endpoint 안정

## DoD

- [ ] SessionDetail 어시 블록 + ChatPanel 어시 블록 모두 마크다운 (코드블록 / 인라인 코드 / 리스트 / 헤딩) 렌더
- [ ] DB `agent_sessions` 에 `cache_write_tokens` / `cache_read_tokens` 컬럼 (BigInteger, NOT NULL, default 0)
- [ ] `CostAccumulator` 가 두 카운트 추적, snapshot() 에 노출
- [ ] `_on_cost_update` 가 DB column 같이 업데이트
- [ ] CostModal (chat panel 의 비용 상세) + SessionDetail header 에 cache 토큰 표시
- [ ] Phase I.3 의 의문 해소: SessionDetail 헤더에 "6 input · 6 output · 3400 cache_write" 처럼 보임
- [ ] 단위 테스트 — markdown 렌더 (스모크), cache token round-trip (accumulator + DB)
- [ ] Live smoke — 새 turn 한 번 돌려서 cache_write_tokens > 0, UI 확인

---

## 작업 항목

### K.1 — Markdown 렌더링

**라이브러리 선택**:
- `marked` (8KB gz) — markdown → HTML
- `dompurify` (20KB gz) — HTML XSS sanitization
- 합 ~28KB gz. 일일 사용 컴포넌트 (chat) 라 정당.

**산출물**:
- `web/package.json` — `marked` + `dompurify` 추가
- `web/src/ui/MarkdownText.tsx` 신규 — `{children: string}` props,
  marked → dompurify → `dangerouslySetInnerHTML`. 코드블록은 `<pre><code>`
  로 출력, 기존 tailwind `prose` 클래스 (이미 설치된 tailwindcss 의 typography
  플러그인 없으면 직접 스타일링)
- `web/src/routes/SessionDetail.tsx` — 어시 블록을 `MarkdownText` 로 교체
- `web/src/chat/ChatPanel.tsx` `EventRow` — 어시 (`text` kind, role≠user)
  를 `MarkdownText` 로 교체. user bubble + `user_message` 는 그대로 plain.

**스타일**:
- inline code: `bg-bg-subtle px-1 rounded font-mono text-[12px]`
- code block: `<pre>` with `bg-bg-subtle p-2 rounded overflow-auto font-mono text-[11.5px]`
- list: `list-disc pl-5`
- headings: 텍스트 사이즈만 키움 (`<h1>` → `text-[15px] font-semibold`)

**왜 prose 플러그인 안 씀**: tailwindcss typography 플러그인은 별도 dep
+ 우리 디자인 토큰 (border / accent) 과 충돌. 6~7 셀렉터 수동 작성이 더 깔끔.

**테스트**:
- `web/tests/ui/markdown-text.test.tsx` (vitest) —
  - 인라인 코드, 코드블록, 리스트, h1~h3 가 적절한 HTML 로 변환
  - XSS payload (`<script>`, `<img onerror=>`) 가 sanitize 됨

---

### K.2 — Cache 토큰 추적 + DB + UI

#### Migration

**파일**: `server/migrations/versions/20260601_xxxxxx_add_cache_tokens.py`

```sql
ALTER TABLE agent_sessions
  ADD COLUMN cache_write_tokens BIGINT NOT NULL DEFAULT 0;
ALTER TABLE agent_sessions
  ADD COLUMN cache_read_tokens BIGINT NOT NULL DEFAULT 0;
```

순수 additive — 기존 row 모두 0. Downgrade 는 drop column.

#### Backend 변경

- `server/src/gapt_server/db/models.py` — AgentSession 에 두 컬럼 추가
- `server/src/gapt_server/agent/hooks/cost_hook.py` — CostAccumulator dataclass 에
  `cache_write_tokens: int = 0` + `cache_read_tokens: int = 0` 필드, snapshot() 갱신
- `server/src/gapt_server/agent/session_registry.py` — `_update_accumulator` 가
  `data.get("cache_write")` / `data.get("cache_read")` 도 acc 에 누적
- `server/src/gapt_server/routers/sessions.py` — `_on_cost_update` 가
  `row.cache_write_tokens = acc.cache_write_tokens` 도 같이 set
- `server/src/gapt_server/routers/sessions.py` — `SessionResponse` 에
  optional `cache_write_tokens` + `cache_read_tokens` (default 0 — 기존 클라이언트 호환)
- `server/src/gapt_server/agent/transcript.py` — Transcript 의 total_*_tokens 옆에
  `total_cache_write_tokens` / `total_cache_read_tokens` 도 노출 (DONE snapshot 의
  값으로 같이 수집)

#### Frontend 변경

- `web/src/api/sessions.ts` — `SessionResponse` + `SessionTranscript` 에 cache
  토큰 optional 필드 추가
- `web/src/chat/CostModal.tsx` — 비용 breakdown 에 cache_write/cache_read row 추가
- `web/src/routes/SessionDetail.tsx` — 헤더 토큰 표시:
  `↑6 input · ↓6 output · ⊕3400 cache_write · ⊖50 cache_read`
- `web/src/routes/SessionsHistory.tsx` — 카드 의 토큰 라인에 `+cache` 작은 hint
  (optional, 카드 가 비좁아지면 skip)

#### Tests

- `server/tests/agent/test_session_recording.py` — `test_cache_tokens_tracked`:
  token.tracked 가 cache_write=1000 들고 오면 acc.cache_write_tokens=1000,
  cost_callback 에 같이 전달
- `server/tests/sessions/test_routes.py` — list 응답에 cache_write_tokens 필드
  존재 (default 0)
- `server/tests/agent/test_transcript.py` — DONE event 에서 cache 토큰 수집

---

## 산출물 요약

```
server/
  migrations/versions/20260601_*_add_cache_tokens.py     (신규 migration)
  src/gapt_server/db/models.py                            (AgentSession 컬럼)
  src/gapt_server/agent/hooks/cost_hook.py                (CostAccumulator 확장)
  src/gapt_server/agent/session_registry.py               (_update_accumulator)
  src/gapt_server/agent/transcript.py                     (total_cache_* 노출)
  src/gapt_server/routers/sessions.py                     (_on_cost_update + SessionResponse)
  tests/agent/test_session_recording.py                   (cache tokens 단위)
  tests/agent/test_transcript.py                          (cache tokens 단위)
  tests/sessions/test_routes.py                           (응답 shape)

web/
  package.json                                             (marked + dompurify)
  src/ui/MarkdownText.tsx                                  (신규)
  src/api/sessions.ts                                      (타입 확장)
  src/chat/CostModal.tsx                                   (breakdown)
  src/chat/ChatPanel.tsx                                   (EventRow markdown)
  src/routes/SessionDetail.tsx                             (markdown + cache 헤더)
  tests/ui/markdown-text.test.tsx                          (신규)

docs/
  plan/m2_phase_k.md                                       (이 파일)
  plan/00_master_plan.md                                   (인덱스)
  progress/m2_phase_k.md                                   (신규)
```

---

## 검증 시나리오

1. 새 채팅 turn ("```python\nprint('hi')\n```\n위 코드 어때?") → ChatPanel 에서
   어시 응답이 코드블록 + 리스트 등으로 렌더.
2. SessionDetail 진입 → 같은 turn 의 어시가 동일하게 마크다운 렌더.
3. 새 turn 종료 직후 `agent_sessions.cache_write_tokens > 0` (sonnet manifest 기준).
4. 비용 대시보드 / SessionDetail 헤더 / CostModal 에 cache_write 토큰 표시.
5. XSS 스모크: 사용자가 `<script>alert(1)</script>` 입력하고 어시가 그대로 echo →
   alert 안 뜸 (dompurify sanitize).

---

## 리스크 + 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| marked + dompurify 번들 ~28KB 증가 | 첫 로딩 약간 느려짐 | 일일 chat / archive UX 라 정당. 측정해서 100KB 넘으면 dynamic import 검토 |
| dompurify 미적용 / 우회 → XSS | 보안 사고 | DOMPurify default config 사용 + 테스트 |
| Migration 이 prod DB 에 컬럼 추가 | downtime / lock | additive default 0 → PG 16 에서 거의 즉시. but 이전 cache token 데이터 는 0 (정확). |
| 기존 클라이언트가 새 응답 필드 보고 crash | UI 깨짐 | optional + default 0 — 새 필드 없이 동작하는 deserialize 가 안전 |
| markdown 렌더가 어시 응답의 일부 형식 깨뜨림 | 가독성 회귀 | tailwind 셀렉터 수동 검증 + 기존 plain `<pre>` 와 시각적 비교 스모크 |

---

## Out of scope

- Markdown table / footnote 등 GFM extensions (기본 `marked` 옵션 만 사용)
- syntax highlighting (`prism` 같은 별도 lib — 별도 cycle)
- Cross-session 검색 (Phase J Out-of-scope 그대로 보류)
- 세션 태그 / 즐겨찾기
- upstream geny-executor 의 model alias PR (계속 보류)

---

## 관련 docs / 메모리

- [`m2_phase_i.md`](m2_phase_i.md) §I.3 — 모델 alias fallback pricing
- [`m2_phase_j.md`](m2_phase_j.md) — archive viewer 라우트
- [[feedback_gapt_pricing_alias_layer]] — cache 토큰이 cost 에는 들어가지만
  카운트가 안 보이던 갭이 K.2 의 동기
