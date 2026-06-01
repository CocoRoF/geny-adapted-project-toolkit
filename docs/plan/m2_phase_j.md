# M2 Phase J — Session archive browser

> **상위**: [`00_master_plan.md`](00_master_plan.md) · [`m2_phase_i.md`](m2_phase_i.md)
>
> Status: done (2026-06-01)
> Estimated: 0.5 작업일 / 1–2 PR
> Depends on: Phase I (transcript export, user_message 영속화)
> Blocks: 없음 — robustness/UX 보강 cycle

## 목적 (한 줄)

Phase I 가 저장 (DB) + 다운로드 (.md) 만 했음. **브라우저 안에서 과거 세션을
열어볼 수 있는** 페이지를 추가해 "vibe-coding archive" 가 일상 동선에 들어오게 한다.

---

## 왜 지금

현재 archive 의 접근 경로:
1. `curl /sessions/{id}/transcript` (개발자 only)
2. ChatPanel 의 다운로드 버튼 → `.md` 파일을 IDE/메모장에서 열기

**브라우저 안에서 보기 = 0** — UI 클릭만으로 "지난 주 무슨 채팅 했지" 못 본다.
`vibe coding 기록물` 이라면서 정작 보러 가는 동선이 없는 게 핵심 누락.

진단:
- `/projects/:pid/sessions` 라우트 X (앞단 router 에 없음, [`web/src/app/router.tsx`](../../web/src/app/router.tsx))
- ChatPanel 은 *active* 세션 1개에만 attach (line 170 ~), archived 못 봄
- 백엔드 `GET /projects/{pid}/sessions` 가 항상 `status != archived` 필터 → archived 목록 못 받음

---

## 진입 조건

- [x] Phase I 완료 (transcript endpoint + user_message persistence)
- [x] `agent_sessions.cost_usd / input_tokens / output_tokens` 가 정상 업데이트됨
  (Phase I.1 live smoke 통과)

## DoD

- [ ] `/projects/:pid/sessions` 페이지에서 active + archived 세션 카드 리스트
- [ ] 각 카드에 date / manifest / first user message snippet / turn count / cost 표시
- [ ] 카드 클릭 → `/projects/:pid/sessions/:sid` 로 이동, transcript inline 렌더
      (download 없이도 한 화면에서 user / assistant / tool 보기)
- [ ] "Workspace 에서 다시 열기" 버튼 — 세션의 workspace 로 이동하여 ChatPanel 에 재attach
- [ ] 백엔드: `GET /projects/{pid}/sessions?include_archived=true` 지원
- [ ] 백엔드: 세션 list response 에 `turn_count`, `first_user_message`,
      `manifest_name` (display 용) 포함

---

## 작업 항목

### J.1 — Backend 세션 list 확장 + 아카이브 접근

**현재 응답** (`SessionResponse`):
```python
id, project_id, workspace_id, env_manifest_id, status,
cost_usd, input_tokens, output_tokens, last_active_at, created_at
```

**추가 필드**:
- `turn_count: int` — count(*) where kind='user_message' from session_events
- `first_user_message: str | None` — 첫 user_message 의 text (preview 용, 100자 cap)

**쿼리 옵션**:
- `?include_archived=true` (default false) — list_sessions 에서 archived 도 반환
- archived session 이 가장 위로 못 오게 sort 는 `created_at DESC` 유지

**Transcript endpoint**: 이미 status 무관하게 동작하니까 그대로.

**산출물**:
- `server/src/gapt_server/routers/sessions.py` — `SessionResponse` 확장,
  `list_sessions` 쿼리 옵션 추가, turn_count + first_user_message 백필
- `server/tests/sessions/test_history.py` — 신규: 3 단위 (active 만 / 아카이브 포함 /
  enriched fields 모양)

---

### J.2 — Frontend SessionsHistory + SessionDetail 라우트

**산출물**:
- `web/src/routes/SessionsHistory.tsx` 신규:
  - 라우트: `/projects/:pid/sessions`
  - 필터 토글: "전체 / active / archived" (default 전체)
  - 카드 리스트: 각 카드에 date, manifest, snippet, turn_count, cost
  - 클릭 → `/projects/:pid/sessions/:sid`
- `web/src/routes/SessionDetail.tsx` 신규:
  - 라우트: `/projects/:pid/sessions/:sid`
  - 헤더: date, manifest, total cost / tokens
  - 본문: transcript JSON 받아서 turn 단위 렌더
    (user bubble + assistant text + tool cards). 별도 markdown library 없이
    Phase I 의 `to_dict` 결과 그대로 React 컴포넌트로 렌더 (이미 모양이 정해져 있음)
  - 액션: "다운로드 (.md)" / "워크스페이스에서 다시 열기"
- `web/src/api/sessions.ts` — `getTranscript(id)` 함수 추가 (JSON 받기)

**왜 markdown library 추가 안 함**:
- 우리 transcript JSON 은 *구조화* 돼 있음 (`{turns: [{user, assistant, tool_uses}]}`)
- React 로 직접 렌더 → 안전 (XSS 우회 차단), 작은 번들, 스타일 통일
- 어시 응답이 markdown 문자열인 경우만 별도 처리. 일반 채팅은 plain text 이므로
  `<pre>` 로 충분. (코드블록까지 syntax highlight 가 필요해지면 그때 react-markdown PR)

---

### J.3 — 네비게이션 / 진입점

**산출물**:
- `web/src/routes/ProjectDetail.tsx` — 헤더 영역에 "세션 히스토리" 링크 추가
  (`/projects/:pid/sessions`)
- (선택) IDE shell 의 ChatPanel 헤더에도 "History" 아이콘 → 같은 라우트

---

### J.4 — Tests + visual smoke + drift + memory

- 백엔드: `tests/sessions/test_history.py` 추가
- 프론트: 시각 검증 — assistant 의 라이브 세션에서 1 turn 후 history 페이지 진입,
  카드 1개 보임, 클릭 → detail 페이지에 turn 1 user/assistant 보임
- progress card `docs/progress/m2_phase_j.md` 갱신
- 메모리: 발생한 패턴 있으면 추가 (없으면 skip)

---

## 산출물 요약

```
server/
  src/gapt_server/routers/sessions.py                          (SessionResponse 확장 + 쿼리 옵션)
  tests/sessions/test_history.py                                (신규)

web/
  src/api/sessions.ts                                            (getTranscript 추가)
  src/routes/SessionsHistory.tsx                                 (신규)
  src/routes/SessionDetail.tsx                                   (신규)
  src/routes/ProjectDetail.tsx                                   (history 링크)
  src/app/router.tsx                                             (2 routes 추가)
  src/i18n/en.ts / ko.ts                                         (몇 개 키)

docs/
  plan/m2_phase_j.md                                             (이 파일)
  plan/00_master_plan.md                                         (인덱스)
  progress/m2_phase_j.md                                         (신규)
```

---

## 검증 시나리오

1. ChatPanel 에서 라이브 세션 1 turn 입력 → 비용 대시보드 cost 증가 확인.
2. ProjectDetail → "세션 히스토리" 링크 클릭 → SessionsHistory 페이지.
3. 카드 클릭 → SessionDetail 페이지에서 user/assistant 바로 보임 (다운로드 없이).
4. "워크스페이스에서 다시 열기" → IDE 로 이동, ChatPanel 이 그 세션 attach.

---

## 리스크 + 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| `turn_count` 쿼리가 session_events 풀스캔 → N+1 | 큰 list 에서 느려짐 | session_id 별 GROUP BY count 한 번 + dict 매핑. 인덱스 (session_id, kind) 가 이미 있음 — 빠름. |
| Transcript JSON 이 큰 세션에서 5MB+ | SessionDetail 로딩 느림 | turn-level lazy render (페이지네이션 미구현 — 일단 전체 로드, 1MB 넘으면 warning) |
| markdown library 없이 assistant 응답 rendering 빈약 | 코드블록 등 표현 손실 | 1차: `<pre>` plain. 사용자가 코드블록 필요하다고 하면 그때 react-markdown 추가 (별도 PR). |

---

## Out of scope

- Cross-session 검색 / 키워드 grep (별도 cycle)
- 세션별 태그 / 노트 / 즐겨찾기
- `cache_write` / `cache_read` 토큰 column 추가 (Phase I.5 후보로 남김)
- Session 내보내기 (zip, multi-session) — single-admin 가정상 불필요

---

## 관련 docs / 메모리

- [`m2_phase_i.md`](m2_phase_i.md) §I.4 — transcript 엔드포인트 + markdown 다운로드
- [[feedback_gapt_pricing_alias_layer]] — session 의 cost 가 어떻게 계산되는지
