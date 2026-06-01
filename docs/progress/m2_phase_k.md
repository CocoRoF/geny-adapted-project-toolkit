# Progress — M2 Phase K (Archive polish)

> Plan: [`../plan/m2_phase_k.md`](../plan/m2_phase_k.md)
> Status: in_progress (started 2026-06-01)

## Timeline

- **2026-06-01** — cycle 개시. Phase J 이후 Out-of-scope 두 가지 (markdown 렌더링,
  cache 토큰 가시화) 가 사용자에게 직접 보이는 갭이라 한 cycle 로 묶음. Plan 카드 작성,
  00_master_plan.md 인덱스 추가.
- **2026-06-01** — K.1 완료. `web/src/ui/MarkdownText.tsx` 신규 (`marked` 18.0 +
  `dompurify` 3.4 install). FORBID_ATTR=["style"] 추가로 inline style XSS 우회 차단.
  tailwind selector-targeted styling (h1~h4 / list / inline code / code block / blockquote /
  link / hr) — typography plugin 안 도입. SessionDetail 의 어시 블록 + ChatPanel 의 EventRow
  text (role≠user) 양쪽 모두 `MarkdownText` 로 교체. User bubble + `user_message` 는
  plain `<pre>` 유지 (사용자 입력 형식 보존). tsc clean.
- **2026-06-01** — K.2 완료.
  Migration `5a9af81931ba` (BigInteger NOT NULL default 0, 양 DB 모두 적용).
  `models.AgentSession.cache_write_tokens / cache_read_tokens` 컬럼 + `CostAccumulator`
  필드 + `snapshot()` 노출. `_update_accumulator` 가 `data.get("cache_write")` /
  `data.get("cache_read")` 도 누적. `_on_cost_update` 가 DB 컬럼 동기화 (unconditional —
  acc snapshot 적용). `SessionResponse` 에 optional field (default 0) 추가. `transcript.py`
  의 `Transcript.total_cache_*` + `to_dict()` + `render_markdown()` 갱신.
  Web: `SessionResponse` / `SessionTranscript` / `CostSnapshot` 타입 확장, SessionDetail
  헤더에 "⊕17188 cache_write" 인라인 표시, SessionsHistory 카드에 "+⊕N" 라인,
  CostModal 에 conditional cache_write / cache_read row. i18n `cost.tokens.cache_{write,read}`
  추가 (en + ko).
- **2026-06-01** — K.3 완료.
  단위: `test_cache_tokens_tracked_in_accumulator` (accumulator + snapshot round-trip),
  `test_cache_tokens_surfaced_in_totals` (transcript build + json + markdown).
  **44/44 pass** (agent + sessions 합산).
  라이브 smoke: 새 turn "Show me a hello world in python" 1회 → DB row 가
  `cost_usd=0.064758, input=6, output=19, cache_write=17188, cache_read=0` 으로 갱신.
  Sonnet 4.6 가격으로 검증: $3.75/M × 17188 + $3/M × 6 + $15/M × 19 ≈ $0.0648 (일치).
  Transcript JSON + 세션 리스트 응답 모두 cache 필드 정상 노출.

## Drift

- **markdown 라이브러리 의존성 추가 결정 (plan 그대로)** — `marked` 18.0 + `dompurify` 3.4.
  bundle ~28KB gz. tailwind typography plugin 안 도입 (셀렉터 직접 작성).
- **MarkdownText 의 XSS 방어 layer**: plan 카드는 dompurify default 만 명시했는데
  실제 코드에서 `FORBID_ATTR=["style"]` 추가로 inline-style 우회 (CSS-inject exfiltration)
  도 차단. plan 보다 더 conservative.
- **Migration revision id** — `5a9af81931ba` (head). `c7d2e9a3f410 → 5a9af81931ba`.
- **CostModal cache rows 가 unconditional 가 아닌 `> 0` 일 때만**: plan 은 "추가" 만
  명시. 빈 행 (0 토큰) 출력하면 모달이 어수선해져서 conditional 처리.
- **MarkdownText component 의 `[&_a]` 셀렉터에 target=_blank 자동 X**: marked default
  는 그대로 `<a href>` 만 emit. target/_rel 추가는 별도 후속 (별도 PR 후보,
  archive 라 외부 링크 클릭이 흔치 않음 → 충분히 안전).
- **upstream geny-executor 의 token.tracked payload 이미 `cache_write` / `cache_read`
  키 사용 확인**: plan 의 추측대로 동작. 추가 어댑터 코드 불필요.
