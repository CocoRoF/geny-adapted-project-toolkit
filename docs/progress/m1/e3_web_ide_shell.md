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
### Cycle 3.2 — 프로젝트 목록 + 생성 플로우 (✅ 완료 — *this commit*)

[plan §3.2](../../plan/m1/e3_web_ide_shell.md#cycle-32-——-프로젝트-목록--생성-플로우-1-pr).

**서버 변경:**
- `server/src/gapt_server/routers/auth.py` — `MeResponse` 에 `orgs: list[OrgMembershipResponse]` 추가. `/api/auth/me` 가 `OrgMembership × Org` join 으로 사용자가 속한 모든 조직 + role 을 반환. `MagicLinkResponse` 의 `token` 필드는 dev 환경 디버깅용 (`MagicLinkAccepted` 는 그대로 — 본 cycle 은 응답 스키마 확장 없이 dev token 의 client surface 만 준비).
- 서버 244 tests 그대로 pass, openapi check 통과 (web/src/api/openapi.json 갱신).

**클라이언트 (3 module + 1 test):**
- `src/api/projects.ts` — `ProjectResponse`, `CreateProjectInput`, `GitProvider` 타입 + `listProjects(orgId?)`, `createProject(input)`, `getProject(id)`, `archiveProject(id)`.
- `src/api/auth.ts` — `MeResponse.orgs` 추가, `OrgMembershipSummary` export.
- `src/routes/NewProjectModal.tsx` — 모달 UI:
  - org 셀렉터 (사용자가 속한 모든 org), display_name, slug (pattern `^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$`, 클라이언트 `aria-invalid` 표시), git_remote_url, git_provider 셀렉터
  - submit → `createProject` → 부모 `onCreated` 콜백
  - 백엔드 `ApiError` 가 `{code}: {reason}` 로 inline alert
  - orgs.length === 0 → "No organisations available" + 폼 비활성화
- `src/routes/ProjectsIndex.tsx` — list view:
  - `useEffect` 로 `listProjects()` 호출 → `loading / ready / error` 상태 머신
  - 카드 그리드: display_name, slug, org_id, git_remote_url; `<Link to="/projects/:id">`
  - "+ 프로젝트" 버튼이 modal open, 생성 성공 시 list prepend (refetch 없이 optimistic UX)
  - "새로고침" 버튼이 refresh 호출
- i18n 카탈로그 17 키 추가 (projects.title, projects.create.*, projects.org, projects.archived, ...) en/ko 양쪽.

**테스트 (`tests/ProjectsIndex.test.tsx`, 4 case):**
- 빈 목록 → empty-state 메시지
- 카드 1개 렌더 + `<Link>` href 검증
- list 500 → role="alert" + exec code surface
- create 모달 open → form submit → 새 카드가 리스트에 추가

**Gate:** server 244 pass (변경 0 regression), openapi check 통과, web 21 test pass (+4), lint/typecheck/format/build clean, 번들 254 kB / 81 kB gz.

#### Plan 카드 대비 변경

- **GitHub Device Flow UI 미구현**: plan §3.2 가 "GitHub OAuth Device Flow UI — user_code 표시, 외부 URL 열기 버튼" + repo 목록 + compose 자동 감지 + env 정의 입력 명시. 본 cycle 은 manual remote URL 입력만 (backend `GithubDeviceFlow` 는 Cycle 2.5 에 있지만 HTTP endpoint 가 `/api/integrations/github/*` 로 surface 안 됨 — backend cycle 이 추가되면 modal 이 wizard 4단계 (Connect → Repo → Compose → Env) 로 확장).
- **org auto-pick**: plan 은 명시 없음. 본 구현은 사용자가 속한 첫 org 를 기본값으로 셀렉터에 noticed. 다중 org 시 사용자가 직접 선택.
- **Card density** ([feedback_no_decorative_chrome] 준수): 이모지/장식 없음. display_name + slug + org_id + remote URL + archived 배지만.
- **archived_at 단순 표시**: plan 은 명시 없음. archived 프로젝트는 카드에 "Archived" 배지 표시. 별도 필터 UI 는 추후.
### Cycle 3.3 — 워크스페이스 진입 + dockview 레이아웃 (2 PR)

#### PR 1 (3.3a) — 프로젝트 디테일 + 워크스페이스 리스트 (✅ 완료 — *this commit*)

**구성 (3 module + 1 test):**
- `src/api/workspaces.ts` — `WorkspaceResponse`, `WorkspaceStatus` (`creating | running | paused | stopped | failed | archived`), `listWorkspaces`, `createWorkspace`, `getWorkspace`, `startWorkspace`, `stopWorkspace`, `deleteWorkspace`.
- `src/routes/NewWorkspaceModal.tsx` — `branch` + optional `worktree_path` 입력, `createWorkspace(pid, input)` 호출, `ApiError` 메시지 surface.
- `src/routes/ProjectDetail.tsx` — `Promise.all([getProject, listWorkspaces])` 로 헤더 + 워크스페이스 행 렌더. 행 액션: running → Stop, stopped/paused → Start, 모든 상태 → Open(`<Link to="/projects/:pid/w/:wid">`). 액션 응답으로 in-place patch (optimistic refresh 없이).
- i18n 16 키 추가 (workspaces.create.*, workspaces.actions.*, workspace.status.paused 등).

**테스트 (3 case):** 헤더 + 워크스페이스 행 렌더, Stop 버튼이 상태를 stopped 로 전환, 빈 워크스페이스 → empty-state.

**Gate:** lint clean, typecheck clean, 24 web test pass (+3), build 260 kB / 82 kB gz.

#### PR 2 (3.3b) — dockview shell + 4 프리셋 레이아웃 (✅ 완료 — *this commit*)

**의존성 추가:** `dockview@6.5.0` (React 19 호환, peer deps 충족).

**구성 (3 module + 1 test + WorkspaceIde 교체):**
- `src/ide/layouts.ts` — 4 `LayoutPreset` (`focus | review | debug | custom`) + `PRESETS: Record<LayoutPreset, SerializedDockview>` 매핑.
  - `focus`: [Tree | Editor | Chat] 가로 3-pane
  - `review`: [Tree | DiffEditor | (Chat 위, CI 아래)] — 우측 컬럼이 세로 분할
  - `debug`: [Tree | (Editor 위, Terminal/Preview 탭 아래)] — 중앙 컬럼이 세로 분할
  - `custom`: 초기에는 focus 기준선, 사용자가 드래그하면 그 스냅샷이 LocalStorage 에 저장되어 다음 진입 시 복원
- `src/ide/panels.tsx` — `<PanelPlaceholder>` 가 `params.kind` 를 받아 "{kind} — lands in a later cycle" 표시. Cycle 3.4 부터 panel 단위로 교체.
- `src/ide/DockviewShell.tsx` — DockviewReact 마운트, 4 프리셋 토글 버튼 + Reset 버튼, LocalStorage 영속 (key: `gapt.ide.layout.<workspaceId>`). 사용자가 드래그하여 레이아웃을 바꾸면 `onDidLayoutChange` 에서 snapshot 캡처 + preset 을 자동으로 "custom" 으로 전환.
- `src/routes/WorkspaceIde.tsx` — `getWorkspace(wid)` 호출 후 헤더 (branch + status) + `<DockviewShell workspaceId={wid}>` 마운트.
- i18n 13 키 추가 (`ide.layout.*`, `ide.panel.*`, `ide.placeholder`, `nav.back_to_project`).

**테스트 (`tests/ide-layouts.test.ts`, 6 case):**
- ALL_PRESETS = [focus, review, debug, custom]
- 각 preset 이 grid + panels 보유
- focus 가 정확히 tree/editor/chat 패널
- review 가 diff + ci 포함
- debug 가 terminal 포함
- custom = focus 기준선

**Gate:** lint clean (1 issue → 수정), typecheck clean, 30 web test pass (+6), build 성공 (585 kB JS / 155 kB gz — dockview 포함, 500 kB 경고는 dynamic chunking 으로 Cycle 3.14 에서 해결).

#### Plan 카드 대비 변경 (Cycle 3.3 통합)

- **백엔드 레이아웃 영속 미구현**: plan §3.3 이 "패널 상태 (`api/workspaces/{wid}/layout`) GET/PUT" 명시. 현재 구현은 LocalStorage 만 — 서버 endpoint 가 추가되면 LocalStorage 가 cache 로 떨어지고 SSOT 는 서버. DoD checklist 의 마지막 항목 "dockview 패널 상태가 사용자별로 LocalStorage + 백엔드에 저장" 중 LocalStorage 부분만 만족 (백엔드는 추후).
- **단축키 미구현**: plan 이 "단축키: `Ctrl+Alt+1/2/3/4` 토글" 명시. Cycle 3.11 (명령 팔레트) 에서 keymap binding 으로 wire-up.
- **헤더 cost/sandbox 미구현**: plan 이 "헤더: Project ▼ / Workspace ▼ / env: dev / cost: $0.42 (라이브)" 명시. 비용 표시는 Cycle 3.10 (cost panel), sandbox status 는 Cycle 3.13 (CI/Audit).
- **status bar 미구현**: plan 이 "상태바: CPU / Mem / sandbox status" 명시. Cycle 3.10/3.13 에서 추가.
- **번들 크기 경고**: dockview 가 ~330 kB 차지하여 빌드 chunk 가 500 kB 초과. Cycle 3.14 (PWA) 에서 dynamic import + manual chunking 으로 분할 예정. M1 dogfood 단계에서는 single-bundle 로 충분.
### Cycle 3.4 — 파일 트리 (✅ 완료 — *this commit*)

[plan §3.4](../../plan/m1/e3_web_ide_shell.md#cycle-34-——-파일-트리-1-pr).

**서버 신규 (3 module + 1 test, 8 case):**
- `server/src/gapt_server/domains/workspaces/files.py` — `WorkspaceFileError` + `TreeEntry` / `FileContent` 데이터클래스 + `list_tree` / `read_file` / `write_file` / `delete_path`. 모두 `SandboxBackend.exec_in` 으로 sandbox 내부에서 실행 → 호스트 FS 노출 없음.
  - 경로 traversal: `_normalise_relative` 가 `..` 세그먼트 거부, root prefix 검증. `find -P` 로 심볼릭 링크 추적 안 함.
  - 자원 한도: tree 결과 `MAX_ENTRIES=2000`, 파일 읽기 `MAX_FILE_BYTES=1 MiB`.
  - 바이너리 처리: cat 결과에 U+FFFD 발견 시 `base64 -w 0 --` 로 재실행하여 binary-safe 응답.
  - write: `mkdir -p` 부모 + `sh -c 'echo $1 | base64 -d > $2'` 패턴 (positional arg 로 shell metacharacter injection 방지).
- `server/src/gapt_server/routers/workspaces.py` — 4 endpoint 추가: `GET /tree?path=`, `GET /file?path=`, `PUT /file?path=`, `DELETE /file?path=`. 모두 `_workspace_for_fs` 헬퍼로 workspace lookup + 프로젝트 멤버십 확인 + sandbox running 상태 확인 (`workspace.fs.not_running` 409). `_http_from_fs_error` 가 stable code → 적절한 HTTP status mapping (400 traversal, 404 not_found, 413 too_large, 500 기타).
- `tests/workspaces/test_files.py` (8 case): `..` traversal 거부, 정상 listing 파싱 + 정렬 (dir 먼저), stat 실패 → 404, size > 1 MiB → 413, write 의 mkdir+sh sequence, root path 삭제 거부, rm -d argv 확인.
- 서버 252 pass (+8), openapi check 통과, 65 KB → 65 KB openapi 스펙.

**클라이언트 신규 (3 module + 1 test, 4 case):**
- `src/api/files.ts` — `TreeEntry`, `FileContent`, `listTree(wid, path)`, `readFile`, `writeFile`, `deleteFile` (URL encoded path query param).
- `src/ide/FileTree.tsx` — 재귀적 `<DirNode>` 컴포넌트. `dirs` cache 에 directory 별 state (`collapsed | loading | ready | error`) 보관. 클릭 시 lazy load, 다시 클릭 → collapse, 파일 클릭 → `onOpenFile(path)`. ApiError 면 code surface.
- `src/ide/panels.tsx` — `<FileTreePanel>` 추가 (dockview panel wrapper). `params.workspaceId` 받아 `<FileTree>` 마운트.
- `src/ide/layouts.ts` — tree panel 을 `placeholder` → `contentComponent: "tree"` 로 교체. workspaceId 는 runtime hydration.
- `src/ide/DockviewShell.tsx` — `loadPreset` 이 layout 의 panels 를 hydrate: `contentComponent === "tree"` 인 panel 에 `params.workspaceId` 주입. components 레지스트리에 `tree → <FileTreePanel>` 추가.

**테스트 (`tests/FileTree.test.tsx`, 4 case):**
- 마운트 시 root 자동 listing (`src` + `README.md` 표시)
- dir 클릭 → children lazy load (`/src` 호출 → `main.py` 등장)
- 파일 클릭 → `onOpenFile("/README.md")`
- 500 응답 → `role="alert"` + code surface

**Gate:** server ruff/mypy clean, 252 server pass, openapi up to date. Web lint/typecheck/format clean, 34 test pass (+4), build 성공 (585 kB / 155 kB gz).

#### Plan 카드 대비 변경

- **가상화 미적용**: plan 이 `@tanstack/react-virtual` 명시. 본 cycle 은 일반 재귀 렌더링 — 1000+ 노드 시 perf 영향 있겠지만 대다수 워크스페이스 트리는 lazy expansion 으로 충분. 가상화는 대형 monorepo 발견 시 추가.
- **우클릭 메뉴 / 파일 생성 / 이름 변경 미구현**: plan §3.4 의 "새 파일/폴더 / 이름 변경 / 삭제" 컨텍스트 메뉴. 본 cycle 은 read-only 트리 + 클릭으로 파일 open 만. 백엔드 `PUT /file` / `DELETE /file` 는 이미 ship 됨 — UI 만 추후.
- **git 상태 dot 미구현**: plan 이 "git 상태 dot (modified/added/untracked)" 명시. 백엔드 git status 엔드포인트가 없음 — `gapt_git` 도구는 sandbox 내부에서 LLM 이 호출하지만 UI 가 직접 호출할 엔드포인트는 아직 없음. Cycle 3.13 (CI/Audit panel) 또는 별도 backend cycle 에서.
- **write/delete UI 미연결**: API 클라이언트 (`writeFile`, `deleteFile`) 는 ship 했지만 UI affordance 는 없음. Cycle 3.5 (Monaco 에디터) 가 자동 저장으로 `writeFile` 호출, Cycle 3.6 (DiffEditor) 가 LLM diff approval 로 호출.
### Cycle 3.5 — Monaco 에디터 + 자동 저장 (✅ 완료 — *this commit*)

[plan §3.5](../../plan/m1/e3_web_ide_shell.md#cycle-35-——-monaco-에디터--자동-저장-2-pr).

**의존성 추가:** `@monaco-editor/react@4.7.0` (Monaco wrapper + lazy CDN loading).

**구성 (3 module + 1 test):**
- `src/ide/editor-store.ts` — `EditorBus` (subscribe/emit pub/sub) + `EditorBusContext` + `useEditorBus` hook. 트리 패널이 파일 클릭 시 `bus.emit(path)`, 에디터 패널이 `bus.subscribe(setOpenPath)`. 두 패널이 dockview 의 별도 React root 에 mount 되어 props 로 연결 불가하기 때문에 context 기반 채널 사용.
- `src/ide/Editor.tsx` — `<FileEditor workspaceId openPath>`:
  - `useEffect(openPath)` → `readFile(wid, path)` → `DocState{path, encoding, text, status: clean}`
  - `onChange` → status=dirty + 300 ms debounce setTimeout → `writeFile(wid, path, {content, encoding})` → status=saving → saved | error
  - 언어 자동 감지 (`LANG_BY_EXT` 매핑 16종: ts/tsx/js/jsx/py/json/md/yaml/toml/sh/rs/go/java/cpp/css/sql ...)
  - binary 파일 (`encoding === "base64"`): non-editable banner + "open from terminal" 안내
  - load 실패 (404 등): status=error + ApiError code surface
  - Monaco options: minimap, automaticLayout, tabSize 2, wordWrap on
- `src/ide/panels.tsx` — `<EditorPanel>` 추가 + `<FileTreePanel>` 가 `bus.emit(path)` 콜백 wire-up.
- `src/ide/layouts.ts` — `editorPanel()` helper, `editor` panel 의 contentComponent 를 `placeholder` → `editor` 로 교체.
- `src/ide/DockviewShell.tsx` — components 레지스트리에 `editor: EditorPanel` 추가. `HYDRATED_PANEL_KINDS = {tree, editor}` — `loadPreset` 가 두 종류 panel 모두에 `workspaceId` 주입. `<EditorBusContext.Provider value={editorBus}>` 로 shell 전체 래핑 (per-workspace instance, 다른 워크스페이스와 cross-talk 없음).

**i18n 8 키 추가** (en/ko): editor.empty / loading / save_failed / binary / dirty / saving / saved.

**테스트 (`tests/Editor.test.tsx`, 4 case):**
- empty-state (openPath=null)
- utf-8 파일 load → Monaco stub textarea 에 content 노출
- base64 응답 → `<editor-binary>` 배너 표시
- 404 응답 → ApiError code (workspace.fs.not_found) inline

**Gate:** lint clean, typecheck clean, 38 web test pass (+4), build 성공 (646 kB JS / 167 kB gz — Monaco wrapper 가 추가됐지만 Monaco 자체는 lazy CDN). format clean.

#### Plan 카드 대비 변경

- **2 PR → 1 PR**: plan 이 2 PR 명시. 본 구현은 단일 PR (편집 + 자동 저장 + 언어 감지 + binary 처리 + 에러 surface) — Monaco wrapper 가 워낙 간단하여 분할 필요성 없음.
- **monaco-vim / vscode keybinding 미구현**: plan 이 "Vim/VSCode 키바인딩 프리셋" 명시. Monaco 자체의 기본 키맵만 사용. Vim 프리셋은 `monaco-vim` 별도 의존성, VSCode keybinding 은 Monaco 가 이미 제공 — Cycle 3.14 또는 사용자 요청 시.
- **1 MB+ 파일 경고 미구현**: plan 이 ">1MB 경고 + lazy load" 명시. 백엔드는 1 MiB 에서 413 반환 (Cycle 3.4 서버 cap) — UI 는 413 → "too_large" code 로 자동 표시. 명시적 경고 모달은 추후.
- **EditorBus 채택**: plan 산출물에 없음. dockview 의 panel-per-React-root 구조 때문에 도입한 패턴. 추후 Monaco DiffEditor (Cycle 3.6) 도 같은 bus 를 통해 diff 카드를 받음.
- **언어 자동 감지 16종**: plan 은 "언어 자동 감지 (path → mime)" 명시. 본 구현은 ext-only 매핑 (mime 검색 안 함). 미지 ext → plaintext fallback.
### Cycle 3.6 — Monaco DiffEditor 카드 (✅ 완료 — *this commit*)

[plan §3.6](../../plan/m1/e3_web_ide_shell.md#cycle-36-——-monaco-diffeditor-카드-1-pr).

**런타임 변경 (1 file):**
- `runtime/src/gapt_runtime/tools/edit.py` — `ToolResult.metadata` 가 이제 `{path, old, new, replaced, all}` 모두 echo. 기존엔 `{replaced, all}` 만 — 채팅 SSE 가 `tool_result` 만 받기 때문에 `args` 를 다시 fetch 할 길이 없었음. 비파괴적 metadata 확장.
- `tests/test_tools_unit.py:183` assertion 업데이트.
- 104 runtime test 그대로 pass.

**웹 신규 (3 module + 1 test, 5 case):**
- `src/chat/diff-util.ts` — `unifiedDiff(old, new)` (newline split), `countLines`, `INLINE_THRESHOLD_LINES = 20` (plan §3.6 의 inline vs side-by-side 임계값).
- `src/chat/DiffCard.tsx` — `<DiffCard workspaceId payload>`:
  - 헤더: 제목 + 경로 + replaced 카운트
  - body: inline `<pre>` 블록 (- 빨강 / + 초록 라인 그룹) — small edit (≤ 20 라인) default
  - large edit (> 20 라인): "Show side-by-side" 토글 → Monaco `<DiffEditor>` (readonly, renderSideBySide, minimap off)
  - footer: side-by-side 토글 + **Revert** 액션
- Revert flow (정직한 post-hoc):
  1. `GET /api/workspaces/{wid}/file?path=...` 로 현재 파일 fetch
  2. base64 인코딩이면 `diff.revert.binary` (415) 에러 — utf-8 만 지원
  3. `payload.all` 이면 split/join 으로 모든 사이트 invert, 아니면 첫 occurrence 만 `String.replace(new, old)` invert
  4. 변경 없으면 `diff.revert.no_op` (409)
  5. `writeFile(wid, path, {content, encoding: "utf-8"})` 로 push
  6. 성공 → "Reverted" 배지, 버튼 disabled

**ChatPanel 통합:**
- `maybeGaptEditPayload(data)`: `data.tool === "gapt_edit"` 이고 `metadata.{path, old, new}` 가 string 이면 `GaptEditPayload` 반환. 그 외 도구는 generic JSON 렌더.
- `<EventRow>` 가 이제 `workspaceId` prop 받음. `tool_result` + 매칭되는 payload → `<DiffCard>`, 아니면 기존 JSON.
- i18n 9 키 추가 (en/ko): diff.title, diff.path, diff.replaced, diff.show_full, diff.show_inline, diff.revert, diff.reverting, diff.revert_done, diff.revert_failed.

**테스트 (`tests/DiffCard.test.tsx`, 5 case):**
- `countLines` 0/1/n 케이스
- `unifiedDiff` newline split
- small edit → path + replaced + inline 표시
- large edit (>20 라인) → 토글 누르면 Monaco DiffEditor 노출
- Revert: GET 으로 fetch → PUT 으로 invert 텍스트 (`"a = 1\n"`) 전송 → "Reverted" 배지

**Gate:** runtime 104 pass, web lint clean (1 issue → 수정), typecheck clean, 46 web test pass (+5), build 성공 (790 kB / 188 kB gz — DiffEditor 가 별도 청크로 분리되지 않아 main bundle 증가, Cycle 3.14 에서 manual chunking).

#### Plan 카드 대비 변경

- **Approve/Deny 라운드트립 대신 post-hoc Revert**: plan §3.6 가 "Approve/Deny/Edit 카드 + Approve 시 `POST /api/workspaces/{wid}/apply-diff`" 명시. 현재 `gapt_edit` 도구는 *호출 즉시 sandbox 안에서 적용* — "preview before apply" 모드가 없음. 두 옵션 중:
  1. 도구 동작 변경 (preview → confirm → apply) — Cycle 2.4 의 도구 계약을 깨뜨림
  2. 적용 후 시각화 + Revert 제공 — 본 cycle 채택
  → M1-E4 가 PolicyEngine REQUIRE_USER_APPROVAL 흐름 + UI confirm 라운드트립 추가 시 pre-apply Approve/Deny 도 자연스럽게 wire-up (Cycle 2.9 `policy_hook.py` 가 이미 REQUIRE_* → block-with-reason 반환).
- **DiffCard 가 file 컨텍스트를 모름**: plan 이 "사이드-바이-사이드 Monaco DiffEditor" 명시 — original 은 변경 *전*, modified 는 변경 *후* 전체 파일을 기대. 본 구현은 `old` vs `new` 만 표시 (find-and-replace 의 두 패턴). LLM 이 실제 보여주고 싶은 건 "이 두 글자 블록이 바뀜" 이므로 직관적. full-file diff 는 M1-E4 의 preview 모드와 함께.
- **`permission_mode = yolo` 자동 적용 미구현**: plan 명시. 백엔드의 `permission_mode` field 가 아직 wire-up 안 됨 (geny-executor 의 `HookEventPayload.permission_mode` 만 존재). 추후 cycle.
- **`Edit` 액션 미구현**: plan 의 "Approve / Deny / Edit" 세 옵션 중 Edit. 본 구현은 Revert 만 — Edit 은 결국 새 `gapt_edit` 호출이라 사용자가 채팅에서 LLM 에 요청하는 게 자연스러움.
### Cycle 3.7 — xterm.js 터미널 (대기)
### Cycle 3.8 — 채팅 패널 SSE 스트리밍 (✅ 완료 — *this commit*, 순서 조정)

[plan §3.8](../../plan/m1/e3_web_ide_shell.md#cycle-38-——-채팅-패널-——-sse-스트리밍-2-pr).

**순서 조정 사유**: plan 카드 순서는 3.6 (DiffEditor) → 3.7 (xterm) → 3.8 (chat) 이지만:
- 3.6 (DiffEditor) 는 LLM 의 `gapt_edit` 도구 호출이 SSE 로 도착해야 의미. 채팅 SSE 가 선행되어야 함.
- 3.7 (xterm) 은 backend PTY endpoint (`WS /api/workspaces/{wid}/pty/{ptyId}`) 가 필요한데 아직 없음.
- 3.8 (SSE chat) 은 backend Cycle 2.10 (sessions API + SSE) 이 이미 완료되어 dependency 충족.
→ 3.8 → 3.6 → 3.9 → 3.7 순서가 자연스러움.

**구성 (4 module + 1 test):**
- `src/api/sessions.ts` — `SessionResponse`, `MessageReplayEntry`, `SessionEventKind` (text/tool_call/tool_result/cost/error/done), `createSession`, `listSessions`, `getSession`, `invokeSession`, `interruptSession`, `replaySessionMessages`, `archiveSession`, `streamUrl(sid, since?)`.
- `src/chat/useSessionStream.ts` — `useSessionStream(sessionId | null)` hook. `EventSource(url, {withCredentials: true})` 를 마운트 후 생성, 6 SSE event listener (`text/tool_call/tool_result/cost/error/done`) 등록 + `lastEventId` 로 seq 추적, `onopen / onerror` 로 status (`idle | connecting | open | closed | error`) 전환. cleanup 에서 `source.close()`. JSON 파싱 실패 → `{raw: <bytes>}` fallback (silent drop 안 함).
- `src/chat/ChatPanel.tsx`:
  - 마운트 시 `listSessions(pid)` 호출 → workspace 매칭 active 세션 자동 attach (페이지 새로고침 후에도 끊기지 않음)
  - 없으면 empty-state + "Start session" 버튼 → `createSession(pid, {workspace_id})` → stream subscribe
  - composer: textarea (Enter 전송, Shift+Enter 줄바꿈), Send / Interrupt / End session
  - cost header: 최근 `cost` 이벤트 reduce 로 `cost.cost_usd / input_tokens / output_tokens` 표시
  - event list: text 는 `<pre>`, tool_call 은 `Tool: <name>`, tool_result 는 raw JSON, error 는 `role="alert" + exec_code + reason`, done 은 완료 배지. cost 는 헤더 fold-in (리스트에서 skip).
  - 모든 액션 에러 → `ApiError.code` inline alert
- `src/ide/panels.tsx` — `<ChatPanelDock>` (dockview wrapper, `projectId + workspaceId` params). FileTree/Editor 와 같은 `useEditorBus` 패턴은 사용 안 함 (chat 은 standalone).
- `src/ide/DockviewShell.tsx` — `chat` component 추가, `HYDRATED_PANEL_KINDS = {tree, editor, chat}` 로 확장, `loadPreset` 가 `projectId` 도 panel params 에 주입. Shell 시그니처 `{ workspaceId, projectId }`.
- `src/ide/layouts.ts` — `chatPanel()` helper, focus/review preset 의 chat panel 을 `contentComponent: "chat"` 으로 교체.
- `src/routes/WorkspaceIde.tsx` — `DockviewShell` 에 `projectId={pid}` 전달.
- i18n 17 키 추가 (en/ko): chat.empty / start / send / interrupt / archive / placeholder / connecting / error / done / invoke_failed / session_failed / tool_call / tool_result / cost.live / cost.tokens / cost.usd.

**테스트 (`tests/ChatPanel.test.tsx`, 3 case):**
- empty-state 에 Start session 버튼 노출
- Start session → POST → composer + cost header 노출
- 마운트 시 active 세션 자동 attach

**EventSource stub**: happy-dom 미구현. tests/ChatPanel.test.tsx 가 `StubEventSource` (open/close + emit helper) 정의 후 `globalThis.EventSource` 패치. 실제 SSE 이벤트 dispatching 은 M1-E4 통합 테스트에 위임.

**Gate:** lint clean (5 issue → 수정), typecheck clean, 41 web test pass (+3), build 성공 (652 kB / 168 kB gz), format clean.

#### Plan 카드 대비 변경

- **2 PR → 1 PR**: plan 이 2 PR (SSE 스트림 + 패널) 명시. 본 구현은 stream 훅 + 패널 + dockview wiring 을 함께 ship (양쪽 모두 짧음). DiffCard 의 receive 측은 Cycle 3.6 에서 분리.
- **react-markdown / react-virtuoso 미적용**: plan 이 markdown render + virtual scroll 명시. 본 구현은 `<pre>` 단순 표시 + 일반 스크롤. markdown 은 Cycle 3.9 (Plan/Act 도구 카드) 에서 도구 결과 표시할 때 추가. virtuoso 는 대량 토큰 스트림 (1000+ chunks) 시 추가.
- **`@file` / `@tool` / slash commands 미구현**: plan 이 명령 자동완성 + 슬래시 명령 명시. Cycle 3.11 (명령 팔레트) 가 키맵 binding 으로 wire-up.
- **자동 스크롤 정지 미구현**: plan 이 "사용자가 위로 올린 상태면 자동 스크롤 정지" 명시. 본 구현은 unconditional auto-scroll. scroll-position 추적은 Cycle 3.11 에서 추가.
- **cost 헤더 = 마지막 cost 이벤트**: plan 이 "1초 디바운스" 명시. backend 는 POST_TOOL_USE 빈도 (~1 Hz max) 로 자연 디바운스 (Cycle 2.9/2.10). 클라이언트는 마지막 cost 이벤트만 reduce — 추가 throttle 불필요.
### Cycle 3.9 — Plan/Act 모드 + 도구 호출 카드 (✅ 완료 — *this commit*)

[plan §3.9](../../plan/m1/e3_web_ide_shell.md#cycle-39-——-planact-모드--도구-호출-카드-2-pr).

**구성 (3 module + 2 test, 8 case):**
- `src/chat/tool-pair.ts` — `ToolPair` 인터페이스 (`call / result / error / running`) + `pairToolEvents(events)` reducer. SSE 가 `tool_call` 과 `tool_result` (또는 `error`) 를 분리 frame 으로 emit → 카드 하나로 묶으려면 walk 후 페어링 필요. 페어링 키: `call_id` (런타임이 echo 시) → 없으면 `name:<tool>`. 동일 tool 이 연속 호출되면 새 call 이 pending 포인터를 덮어쓰는 LRU 동작.
- `src/chat/ToolCallCard.tsx` — 컴팩트 카드:
  - 헤더: tool name + 상태 pill (`Running… / OK / Failed`) + 첫 60자 인자 요약 (key=value 공백 구분) + 토글 버튼
  - body (확장 시): full args JSON + full result JSON (성공) 또는 `exec_code` 박스 + 친화 메시지 (`execMessage()` i18n) + raw reason (에러)
  - `data-testid="tool-card"` + `data-tool-name`
- `src/chat/ChatPanel.tsx` 통합:
  - `toolPairs = pairToolEvents(stream.events)`, `pairedEventSeqs = Set<seq>` — 매핑된 result/error 는 EventRow 가 skip
  - `tool_call` 이벤트 → `<ToolCallCard>` 렌더 (chronology 위치 유지)
  - `gapt_edit` tool_result 는 추가로 `<DiffCard>` 도 렌더 (시각화는 더 유용함, 카드와 공존)
  - 헤더에 Plan/Act 토글 (`aria-pressed`) + plan 모드 시 hint 줄
  - Plan 모드 ON: 메시지 앞에 `(Plan mode) Outline the steps without modifying any files:` prefix 자동 추가
  - 슬래시 명령: `/plan` / `/act` 가 메시지에 있으면 mode 전환 + 나머지 텍스트만 전송 (단독 명령은 invoke 발화 안 함)
  - **Esc 인터럽트**: `window.addEventListener("keydown", ...)` 가 `inflight tool_call` 또는 `pending invoke` 시 Esc 캡처 → `interrupt()` 호출. 세션 없으면 listener 등록 안 함.
  - 헤더 아래 `chat.shortcut.esc` 힌트 텍스트 (세션 활성 시).
- i18n 11 키 추가 (en/ko): chat.mode.plan / act / plan_hint, chat.tool.running / ok / error / expand / collapse / args, chat.shortcut.esc.

**테스트:**
- `tool-pair.test.ts` (4): tool_call+tool_result 페어, running 상태 (outcome 없음), error frame 페어, call_id 로 interleaved 페어링
- `ToolCallCard.test.tsx` (4): Running pill, OK pill, Failed pill + 친화 메시지, expand/collapse 토글

**Gate:** lint clean (1 issue → asString 패턴으로 수정), typecheck clean, 54 web test pass (+8), build 성공 (791 kB / 188 kB gz), format clean.

#### Plan 카드 대비 변경

- **2 PR → 1 PR**: plan 이 2 PR (Plan/Act + 도구 카드) 명시. 두 surface 가 ChatPanel 같은 reducer 안에 자연스럽게 fit — 함께 ship.
- **Plan/Act = 프롬프트 prefix, 별도 서버 모드 없음**: plan 카드는 "ON → 첫 응답은 *계획만*" 명시. 백엔드 `agent_sessions.permission_mode` 가 아직 wire-up 안 됨 (Cycle 2.10 기준). 현재 구현은 클라이언트 prefix — 같은 LLM, 다른 지시. M1-E4 가 permission_mode='plan' 서버 동작 (예: 도구 호출 차단) 추가 시 wrap.
- **인터럽트 단축키 Esc**: plan 이 "Esc 단축키" 명시 — 본 구현은 inflight 가 있을 때만 캡처. 빈 채팅에서 Esc 가 다른 동작 (예: 모달 닫기) 을 가로채지 않도록 가드.
- **`@file` / `@tool` 자동완성 미구현**: plan §3.8 에 명시 — Cycle 3.11 (명령 팔레트) 가 키맵 binding 으로 wire-up.
- **결과 접힘 toggle 만 — markdown render 없음**: plan §3.8 의 "memoized markdown render" 는 텍스트 메시지용. 본 cycle 의 ToolCallCard 는 raw JSON `<pre>` — 도구 결과는 구조화 데이터라 markdown 부적절. text chunk 렌더링용 markdown 은 추후 cycle (사용자 채팅 텍스트가 markdown 인 경우).
- **gapt_edit 의 이중 카드**: tool_call → ToolCallCard, 그리고 tool_result → DiffCard. 의도적 — 카드는 "도구가 호출됐다" 의 메타데이터, DiffCard 는 변경 내용 시각화. 둘 다 가치 있음.
### Cycle 3.10 — 비용 / 컨텍스트 라이브 패널 (✅ 완료 — *this commit*)

[plan §3.10](../../plan/m1/e3_web_ide_shell.md#cycle-310-——-비용--컨텍스트-라이브-패널-1-pr).

**구성 (3 module + 1 test, 8 case):**
- `src/chat/cost-snapshot.ts` — `CostSnapshot` 인터페이스 (`cost_usd / input_tokens / output_tokens / tool_calls / tool_duration_ms / by_tool`) — `gapt_server.agent.hooks.cost_hook.CostAccumulator.snapshot()` (Cycle 2.9) 와 1:1 매핑. `deriveCostSnapshot(events)` 가 events 배열 끝에서부터 최근 `cost` 이벤트 찾아 reduce, 누락 필드 0 fallback. `formatMs(ms)` 헬퍼 (sub-second → "340 ms", 그 외 "1.23 s").
- `src/chat/CostModal.tsx` — `<dialog role="dialog" aria-modal>` 모달:
  - `<dl>` 그리드 (cost_usd / input_tokens / output_tokens / tool_calls / tool_duration_ms)
  - by_tool 도구별 카운트 (descending sort) — 없으면 "No tools" 메시지
  - 닫기 버튼 (헤더 × + 푸터)
- `src/chat/GuardRejectedAlert.tsx` — `role="alertdialog"` 배너 모달. `exec.stage.guard_rejected` exec_code 친화 메시지 (`execMessage` 자동 i18n) + raw reason + Dismiss 버튼.
- ChatPanel 통합:
  - 인라인 cost 헤더가 `<button>` 으로 바뀜 (`onClick → setShowCostModal(true)`, aria-haspopup="dialog")
  - `<CostModal>` conditionally 마운트, snapshot 은 `deriveCostSnapshot` 결과 전달
  - `useEffect` 가 stream.events 의 최근 error 를 watch — `exec_code === "exec.stage.guard_rejected"` 면 `setGuardSeq(seq)` → `<GuardRejectedAlert>` open. `dismissedGuardSeq` ref 가 같은 seq 의 재오픈 방지.
  - 기존 inline `CostSnapshot` 타입 / reducer 제거, `deriveCostSnapshot` 으로 통합.
- i18n 14 키 추가 (en/ko): cost.open / title / close / session_total, cost.tokens.input/output, cost.tool_calls / tool_duration / by_tool / no_tools, cost.guard_rejected.title/body/dismiss.

**테스트 (`tests/cost.test.tsx`, 8 case):**
- `deriveCostSnapshot`: 빈 events → zero snapshot, 여러 cost 이벤트 중 최근 선택, 누락 필드 0 coerce
- `formatMs`: 340 → "340 ms", 1234 → "1.23 s"
- `<CostModal>`: total 표시, by_tool descending list 렌더, 빈 by_tool → "No tools" 메시지
- `<GuardRejectedAlert>`: 친화 메시지 + reason 표시, Dismiss 콜백 호출

**Gate:** lint clean, typecheck clean, 62 web test pass (+8), build 성공 (793 kB / 188 kB gz), format clean.

#### Plan 카드 대비 변경

- **recharts 일별 그래프 미구현**: plan §3.10 이 "일별 그래프 (recharts)" 명시. recharts (~150 KB) 가 번들 부담이고, M1 의 dogfood 단계에서는 세션 스냅샷이면 충분. 추후 Cycle 3.13 (audit panel) 가 일별 집계와 함께 ship 하는 게 자연.
- **1초 디바운스 미적용**: plan 이 "1초 디바운스" 명시. 백엔드 `on_cost_update` (Cycle 2.9) 가 POST_TOOL_USE 빈도 (~1 Hz max) 로 자연 디바운스. 클라이언트 추가 throttle 불필요. M2 Redis 단계에서 frequency 가 변하면 wrap.
- **"더 진행" 정책 완화 가이드 링크 미구현**: plan 이 guard_rejected 모달의 "더 진행 (정책 완화 가이드 링크)" 명시. 본 구현은 Dismiss 만 — 정책 편집 UI 자체가 M1-E4 영역.
- **cost 헤더가 버튼화**: plan 산출물 표에 `CostHeader.tsx` 명시. 본 구현은 ChatPanel 헤더 안의 `<button>` (별도 컴포넌트 분리 안 함) — Cycle 3.11 명령 팔레트에서 cost 관련 액션 추가 시 분리 고려.
### Cycle 3.11 — 명령 팔레트 + 단축키 (✅ 완료 — *this commit*)

[plan §3.11](../../plan/m1/e3_web_ide_shell.md#cycle-311-——-명령-팔레트--단축키-1-pr).

**의존성 추가:** `cmdk@1.1.1` (Radix-backed command primitive + Dialog).

**구성 (5 module + 1 test, 3 case):**
- `src/app/providers/palette-context.ts` — `PaletteAction` 인터페이스 (`id / title / section / keywords? / shortcut? / run`) + `PaletteRegistry` (open / close / isOpen / register / list / subscribe) + `usePalette` hook.
- `src/app/providers/PaletteProvider.tsx` — 상태 관리:
  - `actions: useRef(Map<id, action>)` — id 충돌 시 replace
  - `listeners: useRef(Set<callback>)` — 변경 시 subscriber 모두 notify
  - `Cmd/Ctrl+K` 글로벌 키 핸들러로 toggle, `Esc` 로 close (isOpen 일 때만)
- `src/app/CommandPalette.tsx` — `cmdk`의 `<Command.Dialog>` 마운트:
  - `Command.Input` + `Command.List` + `Command.Empty` + `Command.Group` (section 별) + `Command.Item` (title + keywords 합쳐 value, shortcut kbd)
  - `tick` counter 가 registry 변경 시 리렌더 트리거 (`useEffect`에서 subscribe)
  - 선택 시 `action.run()` → `palette.close()`
- `src/app/usePaletteAction.ts` — `usePaletteAction(action)` hook. 컴포넌트 mount 동안만 register, unmount 시 자동 deregister.
- `src/app/AppPaletteActions.tsx` — 최상위 navigation 액션 (앱 어디서나 호출 가능):
  - `navigate.projects` → `/projects`
  - `navigate.settings` → `/settings`
  - `locale.toggle` → en ↔ ko
  - `auth.sign_out` → `signOut()`
- `DockviewShell.tsx` 가 4 layout preset 액션 (`ide.layout.{focus,review,debug,custom}`) 등록 + `Ctrl+Alt+1..4` 키맵 wire-up (workspace 마운트 동안만).
- `App.tsx` 가 `<PaletteProvider>` 안에 `<AppPaletteActions />` + `<CommandPalette />` + `<AppRouter />` 배치.
- i18n 12 키 (en/ko): palette.open / placeholder / empty / section.* (layout / navigate / session) / action.* (go_projects / go_settings / toggle_locale / sign_out) / shortcut.hint.

**테스트 (`tests/CommandPalette.test.tsx`, 3 case):**
- Ctrl+K 가 팔레트 open + 등록된 액션 노출
- 액션 클릭 → run 호출 + 자동 close
- Esc → close

**Gate:** lint clean (1 issue → tick 패턴으로 수정), typecheck clean, 65 web test pass (+3), build 성공 (842 kB / 197 kB gz — cmdk + Radix dialog ~50 kB 추가, Cycle 3.14 chunking 으로 분리).

#### Plan 카드 대비 변경

- **파일 검색 + 세션 전환 미구현**: plan §3.11 이 "파일 검색 (워크스페이스 트리)", "세션 전환" 명시. 본 cycle 은 글로벌 navigation + 레이아웃 preset + locale + sign-out 만. 파일 검색은 backend `GET /tree` 를 query 기반으로 확장해야 함 (현재는 폴더 listing 만). 세션 전환은 `/api/projects/:pid/sessions` 가 이미 있으므로 ChatPanel 안에서 등록 가능 — Cycle 3.13 또는 별도 follow-up.
- **shortcut 표시 = visual only**: `kbd` 가 액션 옆에 표시되지만 실제 키 처리는 dockview-shell 안에서. cmdk 의 `shortcut` prop 은 visual hint only — global 키 dispatch 는 우리가 직접 wire-up 해야 함. dockview shell 의 Ctrl+Alt+1-4 가 그 예.
- **action registration = render time**: 액션 등록이 컴포넌트 mount 동안만 유효 → 다른 페이지에 가면 그 페이지의 액션은 사라짐. 의도적 — context-aware 팔레트. plan 의 "모든 액션이 팔레트에서 도달 가능" 은 *현재 컨텍스트의* 모든 액션을 의미.
- **cmdk Dialog 의 a11y title**: cmdk 가 Radix Dialog 위에 빌드 → `<DialogTitle>` 요구. `title` prop 사용 (cmdk 가 visually-hidden 으로 wrap). `description` prop 은 cmdk 1.x 에서 지원 안 됨 — 경고는 dev-only warning 으로 무시 가능.
### Cycle 3.12 — 프리뷰 iframe + 외부 공유 (✅ 완료 — *this commit*)

[plan §3.12](../../plan/m1/e3_web_ide_shell.md#cycle-312-——-프리뷰-iframe--외부-공유-1-pr).

**구성 (1 module + 1 test, 4 case):**
- `src/ide/PreviewPanel.tsx`:
  - URL 입력 (localStorage key `gapt.ide.preview.{workspaceId}` 영속, workspace 별 격리)
  - `<iframe src={url}>` 마운트 (URL 없으면 empty-state)
  - Refresh 버튼 → `reloadKey++` → React key change 로 iframe 재마운트
  - "Open in new tab" `<a target="_blank" rel="noopener noreferrer">`
  - Device chips (radio): desktop (parent 100%) / tablet (768px) / phone (390px) → 인라인 `style.width` 변경
  - iframe **sandbox 미설정**: 사용자 자신의 sandbox dev server 가 대상이므로 capability 제한 안 함. 외부 untrusted 콘텐츠가 아님.
- `src/ide/panels.tsx` 에 `<PreviewPanelDock>` 추가 (dockview wrapper)
- `src/ide/DockviewShell.tsx` components 에 `preview: PreviewPanelDock` 등록, `HYDRATED_PANEL_KINDS` 에 `preview` 추가
- `src/ide/layouts.ts` 에 `previewPanel()` helper, `debug` preset 의 preview 패널 contentComponent `placeholder` → `preview` 로 교체
- i18n 9 키 추가 (en/ko): preview.title / empty / url_label / url_placeholder / open_external / refresh / device.{desktop,tablet,phone}

**테스트 (`tests/PreviewPanel.test.tsx`, 4 case):**
- URL 미설정 → empty-state, iframe 없음
- URL 입력 → iframe.src 반영
- LocalStorage 영속 (`gapt.ide.preview.ws1` 키)
- Device chip "Phone" 클릭 → iframe.style.width === "390px"

**Gate:** lint clean (1 issue → generic getByTestId 으로 수정), typecheck clean, 69 web test pass (+4), build 성공 (842 kB / 197 kB gz — 추가 의존성 없음, PreviewPanel 자체는 vanilla React).

#### Plan 카드 대비 변경

- **`{slug}.preview.{domain}` 서브도메인 미적용**: plan 이 "iframe src=`https://{slug}.preview.{domain}/`" 명시. M1-E1 의 Caddy + DNS wildcard 가 production 단계에서 wire-up 됨. 현재 구현은 사용자가 직접 URL 입력 — Vite 의 "Network: http://localhost:5173/" 출력을 그대로 복붙하는 UX. M1-E4 dogfood 단계에서 `https://{slug}.preview.{host}` 자동 채움.
- **QR 코드 미구현**: plan 의 "QR 코드 (모바일 테스트)" → `qrcode.react` 의존성 추가 비용 대비 가치 작음. 모바일 테스트는 `Open in new tab` + 폰에서 같은 URL 입력. 필요 시 Cycle 3.14 에서 추가.
- **자동 리프레시 미구현**: plan 의 "watch 재기동 시 트리거" — backend webhook 또는 file watcher 가 필요. 현재는 manual Refresh. M1-E4 에서 backend 가 build/watch 이벤트 emit 시 wire-up.
- **외부 공유 (cloudflared) 미구현**: plan 의 "외부 공유 토글" — backend cloudflared/ngrok 통합 의존. 별도 cycle / M2.
- **iframe sandbox 결정**: plan 명시 없음. 사용자의 자신의 sandbox dev server 만 대상이라 capability 제한 불필요 — file:// 또는 untrusted URL 입력 시 브라우저 자체 same-origin 정책이 작동.
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
