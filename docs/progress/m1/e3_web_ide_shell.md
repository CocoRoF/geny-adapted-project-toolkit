# M1-E3 Progress — Web IDE Shell

[Plan card](../../plan/m1/e3_web_ide_shell.md) · 14 cycles · 12 작업일 estimate.

## 진입 조건 검증

- [x] M1-E1 backend foundation 완료 (b9... 라인업)
- [x] M1-E2 agent + git + sessions 완료 (8ece16f)
- [x] OpenAPI 자동 export 동작 (`scripts/export_openapi.py` + `web/src/api/openapi.json` 신뢰성 갱신, M1-E2 closure 에서 244 server pass + 갱신 확인)
- [x] `web/` 셸 Phase 0 상태 (Vite + React 19 + react-router-dom 7 + vitest + happy-dom + i18n 기본 카탈로그)

## 시작 시점 인벤토리

- `web/src/main.tsx` — StrictMode + createRoot
- `web/src/app/App.tsx` — i18n 데모 페이지 (단일 화면, 라우팅 없음)
- `web/src/i18n/{index.ts, en.ts, ko.ts, LanguageSwitcher.tsx}` — 카탈로그 + 셀렉터, exec.*.* 코드 일부 채워짐
- `web/src/api/openapi.json` — 백엔드에서 자동 생성된 OpenAPI 3.1 스펙
- `web/tests/{App.test.tsx, i18n.test.ts, setup.ts}` — vitest + happy-dom + @testing-library/react

## Cycle 진행 로그

### Cycle 3.1 — 라우팅 + Auth 셸 + i18n exec.*.* 카탈로그 (✅ 완료 — *this commit*)

[plan §3.1](../../plan/m1/e3_web_ide_shell.md#cycle-31-——-라우팅--auth-셸--i18n-1-pr).

**구성 (10 module + 4 test):**
- `src/api/client.ts` — `apiFetch` 래퍼, `ApiError` (status / code / reason). FastAPI `detail.{code,reason}` 응답을 stable `code: string` 으로 정규화. `apiGet` / `apiPost` / `apiPatch` / `apiDelete` 헬퍼. `credentials: "include"` 기본값.
- `src/api/auth.ts` — `fetchMe`, `requestMagicLink`, `completeMagicLink`, `logout` — 백엔드 `/api/auth/*` 엔드포인트 매핑.
- `src/app/providers/auth-context.ts` + `AuthProvider.tsx` — `<AuthProvider>` 가 마운트 시 `/me` 폴, `status: idle → signed_in / signed_out / error` 상태 머신. inflight ref 로 동시 호출 코어레스. (`useAuth` hook 은 context 모듈에 분리 — react-refresh/only-export-components 충족).
- `src/app/providers/i18n-context.ts` + `I18nProvider.tsx` — localStorage 영속, `ko-*` 브라우저 기본 KO, 그 외 EN. `t(key)` + `execMessage(code)` 헬퍼 노출.
- `src/app/RequireAuth.tsx` — `<RequireAuth>` 가 `idle → Loading…`, `signed_out → <Navigate to="/login" />`, `error → 알림 배너`.
- `src/app/layouts/AppShellLayout.tsx` — 인증 라우트 공통 chrome (header / locale switcher / sign-out 버튼 / main / footer).
- `src/app/router.tsx` — `<Routes>` 트리:
  - public: `/login`, `/auth/callback`
  - guarded: `/projects`, `/projects/:pid`, `/projects/:pid/w/:wid`, `/settings/*`
  - fallback: `/` → `/projects`, `*` → `/projects`
- `src/routes/Login.tsx` — 이메일 입력 + 매직 링크 요청 + 보낸 상태 표시 + 인증된 사용자 자동 redirect (location.state.from 우선).
- `src/routes/AuthCallback.tsx` — `?token=` → `completeMagicLink` → `refresh()` → `<Navigate to="/projects" />`. 실패 시 alert 섹션.
- `src/routes/{ProjectsIndex, ProjectDetail, WorkspaceIde, Settings}.tsx` — 라우터 destination placeholder (실제 내용은 Cycle 3.2 / 3.3 / 3.13).
- `src/app/App.tsx` — `<BrowserRouter>` + `<I18nProvider>` + `<AuthProvider>` + `<AppRouter />`.

**i18n exec.*.* 카탈로그 확장:**
- `geny_executor.errors` 의 모든 family (api, cli, mcp, mutation, pipeline, session, stage, tool) 1+ 키씩 en/ko 양쪽에 추가 (총 35개 exec.*.* 키 × 2 locale).
- `execMessage(code, locale)` 가 unknown 코드 → raw code fall-through (UI 가 알 수 없는 코드를 grep 가능한 형태로 surface).
- i18n.test.ts 가 catalogs parity + family coverage 둘 다 검증.

**테스트 (`tests/{App,Login,api-client,i18n}.test.tsx`, 17 case, all green):**
- `App.test.tsx` (4): unauthed → /login redirect, authed → projects placeholder + 헤더의 user.email 버튼, language switcher 2 옵션, /me 503 → 에러 배너
- `Login.test.tsx` (2): magic link 전송 success state, 백엔드 400 → alert 박스 + code 표시
- `api-client.test.ts` (4): JSON body 전송, 204 no-content, FastAPI detail envelope → ApiError 매핑, 비JSON 응답 → `http.<status>` 코드
- `i18n.test.ts` (7): en/ko parity, t() locale 반환, exec.* 키 양쪽 존재, 비어있지 않음, execMessage known/unknown, family coverage

**Gate:** pnpm lint clean (0 errors, 0 warnings), pnpm typecheck clean, pnpm test 17/17 pass, pnpm build 성공 (248 kB JS / 79 kB gz), pnpm format:check clean.

#### Plan 카드 대비 변경

- **`/projects/:pid/w/:wid` slug**: plan 카드는 `/workspaces/:wid` 와 `/w/:wid` 둘 다 흩어져 있음. 라우터는 `/w/:wid` 로 통일 (URL 짧음, IDE 워크스페이스 진입 빈도 높음).
- **로그인 후 redirect**: plan 은 명시 없음. 현재 구현은 location.state.from (RequireAuth 가 전달) 우선, 없으면 `/projects` — 사용자가 보호 페이지 접근 → /login → 인증 → 원래 페이지 복귀.
- **dev magic token 표시**: 백엔드 `MagicLinkResponse.token` 이 dev 환경에서 채워질 때 UI 에 노출 (data-testid="dev-magic-token"). prod 에서는 token 필드 omit 되어 안 보임. CI/CD 우회 위험 없음 (백엔드가 prod 빌드에서 토큰 안 채움).
- **route 모듈 분할**: plan 산출물 표가 `routes/login.tsx` 등 소문자 + 점 표기. 본 cycle 은 PascalCase 파일명 + barrel 안 함 — vitest/eslint 모두 일관성 위해 한 패턴.
- **provider 분리**: react-refresh/only-export-components 위반 회피 목적으로 `AuthProvider.tsx` / `auth-context.ts` 분리 (`useAuth` hook 은 context 모듈에). I18nProvider 도 동일 패턴.
### Cycle 3.2 — 프로젝트 목록 + 생성 플로우 (대기)
### Cycle 3.3 — 워크스페이스 진입 + dockview 레이아웃 (대기, 2 PR)
### Cycle 3.4 — 파일 트리 (대기)
### Cycle 3.5 — Monaco 에디터 + 자동 저장 (대기, 2 PR)
### Cycle 3.6 — Monaco DiffEditor 카드 (대기)
### Cycle 3.7 — xterm.js 터미널 (대기)
### Cycle 3.8 — 채팅 패널 SSE 스트리밍 (대기, 2 PR)
### Cycle 3.9 — Plan/Act 모드 + 도구 호출 카드 (대기, 2 PR)
### Cycle 3.10 — 비용 / 컨텍스트 라이브 패널 (대기)
### Cycle 3.11 — 명령 팔레트 + 단축키 (대기)
### Cycle 3.12 — 프리뷰 iframe + 외부 공유 (대기)
### Cycle 3.13 — CI / Audit / Logs 패널 (대기)
### Cycle 3.14 — PWA + 다크 모드 + 접근성 (대기)

## DoD 진행

[Plan 카드](../../plan/m1/e3_web_ide_shell.md) DoD 11 개:

- [ ] `/projects` 라우트: 좌측 트리 + 워크스페이스 진입
- [ ] `/projects/{pid}/workspaces/{wid}` 라우트: dockview 풀 레이아웃
- [ ] 레이아웃 프리셋 4종 (Focus / Review / Debug / Custom) 토글
- [ ] Monaco 에디터: 파일 트리 클릭 → 열림 + 편집 + 자동 저장(300ms) + git dot
- [ ] Monaco DiffEditor: LLM 변경 사항 side-by-side + Approve/Deny 카드
- [ ] xterm.js: 데몬 PTY attach + 다중 탭 + 사용자/LLM 별도 PTY
- [ ] 채팅 패널: SSE 토큰 스트리밍 + Plan/Act 모드 + 도구 호출 카드 + diff 카드 + 비용 라이브 헤더
- [ ] 명령 팔레트 (`Ctrl+K`) — 파일/세션/액션 통합 검색
- [ ] `exec.*.*` 에러 코드가 i18n catalog 로 사람 친화 표시 (en/ko)
- [ ] PWA manifest + service worker (오프라인 셸 최소)
- [ ] dockview 패널 상태 사용자별 LocalStorage + 백엔드 저장

## Drift (cycle 종료 시 누적 기록)

*(아직 종료되지 않음)*
