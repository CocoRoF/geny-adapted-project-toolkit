# Progress — M2 Phase J (Session archive browser)

> Plan: [`../plan/m2_phase_j.md`](../plan/m2_phase_j.md)
> Status: in_progress (started 2026-06-01)

## Timeline

- **2026-06-01** — cycle 개시. Phase I 가 archive 의 *저장* + *.md 다운로드* 만 했고
  *브라우저 안에서 보기* 가 빠진 것 확인. Plan 카드 작성, 00_master_plan.md 인덱스 추가.
- **2026-06-01** — J.1 완료. `SessionResponse` 에 `turn_count` + `first_user_message`
  optional field, `list_sessions` 에 `include_archived=true` query 추가. 추가 쿼리 2개
  (COUNT/GROUP BY + DISTINCT ON) 로 list-view 페이지에서 N+1 회피.
  단위 테스트 `test_list_sessions_enriched_and_archive_filter` 추가, archive → default 빈 →
  include_archived=true → enriched row 1개 라운드트립 검증.
- **2026-06-01** — J.2 완료. `web/src/routes/SessionsHistory.tsx` + `SessionDetail.tsx` 신규.
  History 페이지: filter chips (전체/active/archived) + 카드 (status, manifest, snippet,
  turn count, cost / tokens, relative time). Detail 페이지: 메타 헤더 (status, manifest,
  total cost/tokens, turn 수) + .md 다운로드 + "워크스페이스 열기" + 인라인 turn 렌더
  (user bubble, assistant block, tool details with collapsible input/output).
  `getSessionTranscript(sid)` API 추가, transcript JSON type 정의.
- **2026-06-01** — J.3 완료. ProjectDetail 헤더에 "세션 히스토리 →" 링크 추가
  (Environments 옆).
- **2026-06-01** — J.4 완료. 사이드 회귀 발견 및 수정:
  - `test_invoke_and_replay_messages` / `test_invoke_endpoint_kicks_off_runner` —
    Phase I.2 USER_MESSAGE event 추가로 `["text","done"]` → `["user_message","text","done"]`
    expectation 갱신.
  - `test_oneshot_captures_tool_calls` — pre-existing 버그 (`tool_calls[0]["name"]` →
    실제 payload 에는 `tool`/`tool_name` 만 있음), 같이 fix.
  - 총 42/42 pass.
  Live smoke: `GET /projects/{pid}/sessions` 가 enriched 응답 (turn_count, first_user_message)
  반환 확인. tsc clean, 새 파일 lint 0 error.

## Drift

- **사이드 회귀 fix 2건 같이 처리** — Plan 카드는 새 페이지만 명시했으나 Phase I.2 의
  USER_MESSAGE event 가 추가되며 기존 oneshot/route 테스트의 이벤트 순서 기대치 갱신이
  필요했음. 별도 cycle 만들 가치는 없어서 J.4 안에 함께 처리, 메모리 / drift 에 기록.
- **`SessionDetail` 의 markdown 라이브러리 안 함** — Plan 의 "raw markdown 으로 충분"
  결정 유지. 어시 응답이 코드블록 많이 쓰는 manifest 가 등장하면 그때 `react-markdown`
  도입 PR.
- **i18n 키 추가 안 함** — 새 페이지가 한국어 일부 (세션 히스토리 / 워크스페이스 열기 등)
  를 직접 박아넣음. 추후 SessionsHistory / SessionDetail 의 모든 텍스트를 i18n catalog 로
  옮기는 작업은 별도 PR 후보 (single-admin 운영자가 한국어 사용 가정).
