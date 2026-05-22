# M1-E3: Web IDE 셸 (Monaco / dockview / xterm / 채팅 SSE / diff)

> Status: planned
> Estimated: 12 작업일 / 14 PR
> Depends on: M1-E1 (백엔드 API), M1-E2 (SSE 스트림 + 도구)
> Blocks: M1-E4
> Relates to: [`../../08_web_ide_ux.md`](../../08_web_ide_ux.md) (전체)

## 목적 (한 줄)
사용자가 브라우저 한 탭에서 *데스크탑 Cursor에 준하는* IDE-like 경험으로 GAPT의 모든 기능에 도달한다 — 좌측 프로젝트 트리, 중앙 Monaco 에디터 + 사이드-바이-사이드 diff, 우측 LLM 채팅(1급), 하단 xterm 터미널, 프리뷰 iframe, CI/Audit/Cost 패널.

## 진입 조건
- [ ] M1-E1, M1-E2 통과
- [ ] OpenAPI 자동 type generation 동작 (E1 cycle 1.12)
- [ ] [`08`](../../08_web_ide_ux.md) §8.3 (레이아웃), §8.4 (채팅), §8.6 (터미널) 일독

## DoD
- [ ] `/projects` 라우트: 좌측 트리 + 프로젝트 클릭 → 워크스페이스 진입
- [ ] `/projects/{pid}/workspaces/{wid}` 라우트: dockview 풀 레이아웃
- [ ] **레이아웃 프리셋 4종** (Focus / Review / Debug / Custom) 토글
- [ ] Monaco 에디터: 파일 트리에서 클릭 → 열림 + 편집 + 자동 저장(300ms) + git 상태 dot
- [ ] Monaco DiffEditor: LLM 변경 사항을 side-by-side로 표시 + Approve/Deny 카드
- [ ] xterm.js: 데몬 PTY로 attach + 다중 탭 + 사용자/LLM 별도 PTY
- [ ] 채팅 패널: SSE 토큰 스트리밍 + Plan/Act 모드 + 도구 호출 카드 + diff 카드 + 비용 라이브 헤더
- [ ] 명령 팔레트 (`Ctrl+K`): 파일/세션/액션 통합 검색
- [ ] `exec.*.*` 에러 코드가 i18n catalog로 사람 친화 표시 (영/한)
- [ ] PWA manifest + service worker (오프라인 셸은 추후)
- [ ] dockview 패널 상태가 사용자별로 LocalStorage + 백엔드에 저장 (재진입 시 복원)

## 작업 항목 (세부)

### Cycle 3.1 — 라우팅 + Auth 셸 + i18n (1 PR)
- React Router v7 라우트:
  - `/login` (magic-link 입력)
  - `/auth/callback`
  - `/projects` (목록)
  - `/projects/:pid` (오버뷰)
  - `/projects/:pid/w/:wid` (워크스페이스 IDE)
  - `/settings/*`
- `Auth` 컨텍스트 + `/api/me` polling
- i18n `en.ts` / `ko.ts`:
  - 모든 `exec.*.*` 코드별 친화 메시지 ([04](../../04_llm_agent_layer.md) §4.10 표)
  - UI 카피
- LanguageSwitcher (헤더)

### Cycle 3.2 — 프로젝트 목록 + 생성 플로우 (1 PR)
- `/projects`:
  - 카드 목록: 이름, 마지막 활동 시각, 활성 워크스페이스 카운트
  - "+ 프로젝트 추가" → 모달:
    1. GitHub 연결 (OAuth Device Flow UI — user_code 표시, 외부 URL 열기 버튼)
    2. 사용자 GitHub repo 목록 (백엔드가 `list_user_repos` 호출)
    3. 선택 → compose 파일 자동 감지 → 확인 화면
    4. 환경(dev/prod) 정의 입력
- [[feedback_no_decorative_chrome]]: 카드는 dense, 이모지/장식 없음

### Cycle 3.3 — 워크스페이스 진입 + dockview 레이아웃 (2 PR)
- `dockview` (React 어댑터) 셋업
- 4 프리셋:
  - **Focus**: [Tree | Editor | Chat]
  - **Review**: [Tree | DiffEditor | Chat + CI]
  - **Debug**: [Tree | Editor | Terminal | Preview]
  - **Custom**: 사용자 dragging
- 패널 상태 (`api/workspaces/{wid}/layout`) GET/PUT
- 단축키: `Ctrl+Alt+1/2/3/4` 토글
- 헤더: Project ▼ / Workspace ▼ / env: dev / cost: $0.42 (라이브)
- 상태바: CPU / Mem / sandbox status

### Cycle 3.4 — 파일 트리 (1 PR)
- 가상화 (`@tanstack/react-virtual`) — 대형 레포 대비
- 노드 클릭 → 에디터 탭으로 open
- git 상태 dot (modified/added/untracked)
- 우클릭 메뉴: 새 파일/폴더 / 이름 변경 / 삭제 (확인 모달)
- 백엔드 API: `GET /api/workspaces/{wid}/tree?path=`, `POST /api/workspaces/{wid}/files`, `DELETE`...

### Cycle 3.5 — Monaco 에디터 + 자동 저장 (2 PR)
- Monaco React wrapper (`@monaco-editor/react`)
- 언어 자동 감지 (path → mime)
- 자동 저장 300ms 디바운스 → `PATCH /api/workspaces/{wid}/files {path, patch_or_full}`
- 무거운 파일 (>1MB) 경고 + lazy load
- 단축키: Vim/VSCode 키바인딩 프리셋 (Monaco built-in extension `monaco-vim`)
- minimap, bracket matching, code folding, find/replace, multi-cursor

### Cycle 3.6 — Monaco DiffEditor 카드 (1 PR)
- LLM이 `gapt_edit` 도구 호출 → SSE `event: tool_result` 수신 → 채팅에 *diff 카드* 컴포넌트 표시:
  - 작은 변경 (<20줄): 인라인 unified diff
  - 큰 변경: 사이드-바이-사이드 Monaco DiffEditor
- 카드 액션: Approve / Deny / Edit
- Approve → `POST /api/workspaces/{wid}/apply-diff` → 백엔드가 실제 적용 + git 상태 갱신
- `permission_mode = yolo`인 경우 자동 적용 + 사후 요약

### Cycle 3.7 — xterm.js 터미널 (1 PR)
- `xterm.js` v5 + addons (fit / web-links / search)
- 데몬 PTY 양방향: `WS /api/workspaces/{wid}/pty/{ptyId}` (M1-E1 cycle 1.9)
- 다중 탭 (한 워크스페이스에 N개)
- 분할 (가로/세로)
- 사용자/LLM PTY *별도*. 사용자가 "현재 터미널 LLM에 공유" 명시 토글 (LLM이 stdin/stdout 접근)
- 최근 10k 라인 로컬 저장

### Cycle 3.8 — 채팅 패널 — SSE 스트리밍 (2 PR)
- `EventSource` (또는 `fetch-event-source`로 POST + SSE) → 토큰 누적
- React `useReducer` 패턴, memoized markdown render (`react-markdown` + `rehype-highlight`)
- 메시지 가상 스크롤 (`react-virtuoso`)
- 입력창:
  - Shift+Enter 줄바꿈, Enter 전송
  - `@file path/...` 자동완성 (트리에서 검색)
  - `@tool ToolName` 직접 호출
  - 슬래시: `/plan` / `/act` / `/review` / `/deploy` / `/cost` / `/clear`
- 토큰 단위 코드블럭 syntax highlight 점진 적용
- 자동 스크롤 정지 (사용자가 위로 올린 상태면)

### Cycle 3.9 — Plan/Act 모드 + 도구 호출 카드 (2 PR)
- Plan 모드 토글 (헤더):
  - ON → 첫 응답은 *계획만* (코드 변경 X)
  - 사용자 검토 후 "Act" 클릭 → 계획 실행
- 도구 호출 카드 컴포넌트:
  - 도구 이름 + 인자 요약 + 진행 상태 (running/ok/error)
  - 결과 확장 (긴 출력은 접힘)
  - 에러 시 `exec.*.*` 코드 + 사람 친화 메시지 (i18n)
- 인터럽트 버튼 (Esc 단축키) → `POST /api/sessions/{sid}/interrupt`

### Cycle 3.10 — 비용 / 컨텍스트 라이브 패널 (1 PR)
- 헤더에 *세션 누적 USD* 표시 (1초 디바운스)
- 상세 모달:
  - 모델별/요청별 토큰
  - 도구별 비용
  - 일별 그래프 (recharts)
- `exec.stage.guard_rejected` 발생 시 비용 cap 도달 모달 — "더 진행" (정책 완화 가이드 링크) / "중단"

### Cycle 3.11 — 명령 팔레트 + 단축키 (1 PR)
- `Ctrl+K` 모달 (cmdk 라이브러리):
  - 파일 검색 (워크스페이스 트리)
  - 세션 전환
  - 액션 (Deploy, Layout switch, Settings, ...)
  - 단축키 표시
- 모든 액션이 팔레트에서 도달 가능 — Cursor/Linear 패턴

### Cycle 3.12 — 프리뷰 iframe + 외부 공유 (1 PR)
- 프리뷰 패널:
  - `<iframe src="https://{slug}.preview.{domain}/">` (M1-E1 Caddy subdomain — M1-E4에서 등록)
  - "외부 브라우저로 열기" 버튼 + QR 코드 (모바일 테스트)
  - 자동 리프레시 (watch 재기동 시 트리거)
  - 외부 공유 토글 (cloudflared, opt-in)
- 디바이스 사이즈 시뮬레이션 (responsive)

### Cycle 3.13 — CI / Audit / Logs 패널 (1 PR)
- CI 탭: GitHub Actions runs 라이브 스트림 (`GET /api/projects/{pid}/ci/runs` + `WS .../runs/{id}/logs`)
- Audit 탭: 세션/프로젝트 이벤트 필터링 (action / outcome / ts range)
- Logs 탭: 사용자 선택 compose 서비스 stdout 라이브
- 검색 + 필터 + 시간대 변경

### Cycle 3.14 — PWA + 다크 모드 + 접근성 (1 PR)
- `vite-plugin-pwa` — manifest + service worker (오프라인 셸은 최소)
- 다크 모드 토글 (CSS variables, Tailwind dark:)
- 키보드 only 탐색 가능
- focus ring 명시 — [[feedback_no_decorative_chrome]] 위배 안 함

## 산출물
```
web/src/
├── app/
│   ├── App.tsx
│   ├── router.tsx
│   ├── providers/{AuthProvider.tsx, I18nProvider.tsx, ThemeProvider.tsx}
│   └── layouts/{AuthLayout.tsx, IdeShellLayout.tsx}
├── routes/
│   ├── login.tsx
│   ├── auth.callback.tsx
│   ├── projects.index.tsx
│   ├── projects.new.tsx
│   ├── projects.$pid.tsx
│   ├── projects.$pid.w.$wid.tsx
│   └── settings.*
├── ide/
│   ├── DockviewShell.tsx
│   ├── layouts/{Focus.tsx, Review.tsx, Debug.tsx}
│   ├── FileTree.tsx
│   ├── Editor.tsx
│   ├── DiffEditor.tsx
│   ├── Terminal.tsx
│   ├── Preview.tsx
│   ├── CommandPalette.tsx
│   └── StatusBar.tsx
├── chat/
│   ├── ChatPanel.tsx
│   ├── MessageList.tsx
│   ├── MessageInput.tsx
│   ├── ToolCallCard.tsx
│   ├── DiffCard.tsx
│   ├── PlanCard.tsx
│   ├── CostHeader.tsx
│   └── useSession.ts                  # SSE 훅
├── ci/{CiPanel.tsx, RunLogs.tsx}
├── audit/{AuditPanel.tsx, AuditFilters.tsx}
├── api/
│   ├── client.ts
│   ├── types.ts                       # OpenAPI 자동 생성
│   └── sse.ts
└── i18n/{en.ts, ko.ts, exec_codes.ts}

public/manifest.webmanifest
vite.config.ts (PWA 플러그인 추가)
```

## 검증 시나리오
1. 새 사용자 magic-link 로그인 → `/projects` → "+ 프로젝트 추가" → GitHub OAuth Device Flow → 외부 repo 등록까지 5분 내.
2. 워크스페이스 진입 → 5초 안에 IDE 셸 렌더 + 파일 트리 로드.
3. 채팅 "README 한국어 요약" → 첫 토큰 < 1.5s, 전체 응답 < 30s.
4. LLM이 `gapt_edit` 호출 → diff 카드 표시 → Approve → 파일 갱신 + 트리에 git modified dot.
5. 터미널 탭 새로 열기 → 즉시 PTY 연결 + `pwd` 응답.
6. `Ctrl+K` → "Deploy to dev" 검색 → 클릭 → 배포 모달 표시 (M1-E4가 실 동작).
7. Plan 모드 ON → 채팅 → 계획만 응답 → "Act" 클릭 → 계획 실행.
8. 일부러 cost cap 초과 → `exec.stage.guard_rejected` 모달 표시 + i18n 메시지.
9. 폰에서 PWA 설치 → 채팅 + 프리뷰 + 단순 액션 가능.

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|
| Monaco 번들 크기 (5MB+) | 초기 로드 느림 | 라우트 기반 코드 분할, prefetch hint |
| dockview 학습 곡선 | 시간 손실 | 우선 4개 프리셋만, custom drag은 안정화 후 |
| SSE 연결 끊김 (proxy timeout) | 사용자 짜증 | 자동 재연결 + `since` 기반 리플레이 |
| 가상 스크롤 + Monaco가 메모리 ↑ | 긴 세션에서 탭 느려짐 | 메시지 압축 (오래된 것은 요약 본만 메모리에, 클릭 시 fetch) |
| PTY가 ANSI 색상 → markdown 안 호환 | 작음 | xterm은 native, 채팅과 별도 |
| LLM이 diff 카드 N개를 한 번에 쏟아냄 | UI 혼잡 | 카드 그룹핑 + "모두 Approve" |
| 단축키 충돌 (Monaco vs 브라우저 vs 명령 팔레트) | 중 | 단축키 등록 우선순위 명시, Settings에서 변경 |
| OpenAPI 자동 생성이 nullable 타입에서 깨짐 | 중 | `openapi-typescript` 옵션 조정, 수동 패치 가이드 |

## 관련 docs
- [`../../08_web_ide_ux.md`](../../08_web_ide_ux.md) (전체)
- [`../../04_llm_agent_layer.md`](../../04_llm_agent_layer.md) §4.10 (UI 코드 매핑)
- [`../../07_cicd_and_preview.md`](../../07_cicd_and_preview.md) §7.13 (UI 통합 요구)
