# Phase N — Scaffold-based new project creation

> Status: 사용자 검토 완료 2026-06-04, 실행 중
> Predecessor: Phase M (deep-review remediation)
> Goal: GAPT 가 빈 GitHub 레포를 만들고, opinionated 프리셋으로 초기 스택을
> 푸시하고, 곧바로 IDE 에서 편집/배포할 수 있게 한다.

## 1. 동기

지금까지 GAPT 는 "이미 만들어진 GitHub 레포를 가져와서 작업" 하는 **import-only**
도구였습니다. "+ 새 프로젝트" 버튼은 사실 "기존 레포 등록" 이라서, 사용자가 진짜 무에서
시작하려면 GitHub 가서 레포 만들고 README 푸시한 다음 GAPT 로 돌아와야 했습니다.

Phase N 은 그 빈틈을 메꿉니다:

- 기존 "+ 새 프로젝트" → **"프로젝트 불러오기"** 로 라벨 변경 (동작은 그대로)
- 신규 "+ 새 프로젝트" → 진짜 새 프로젝트 위저드:
  - GitHub 레포 이름 입력 → GAPT 가 빈 레포 생성
  - opinionated 프리셋 선택 (Full-stack, Backend-only, Frontend SPA, Static, Empty)
  - 프리셋이 README + docker-compose + 샘플 소스 + GAPT 매니페스트를 초기 커밋
  - 그 시점에 GAPT 프로젝트 레코드 + 초기 워크스페이스가 자동 생성되어 IDE 진입

## 2. 비목표 (v1 scope-out)

- Organization 레포 생성 (`POST /orgs/{org}/repos`) — v1 은 `POST /user/repos` 만
- 기존 레포에 scaffold 덮어쓰기 — v1 은 무조건 새 레포에만
- 프리셋 마켓플레이스 / 사용자 등록 프리셋 — v1 은 서버 내장 5 종
- 다중 워크스페이스 분기 동시 생성 — v1 은 메인 브랜치 워크스페이스 한 개
- CI 워크플로 파일 자동 생성 — v1 은 docker-compose + Dockerfile 까지만

## 3. 프리셋 카탈로그 (v1: 5 종)

각 프리셋은 `(stack_summary, scaffold_files, gapt_env_defaults, option_schema)` 로
구성됩니다. 모든 프리셋은 공통적으로 `README.md`, `.gitignore`,
`.gapt/manifest.json` (실은 GAPT 가 매니페스트 파일을 별도로 두진 않으므로 메타
파일만), `LICENSE` (MIT 기본) 을 포함합니다.

### 3.1 `empty` — 빈 프로젝트
- **스택**: 없음
- **파일**: `README.md`, `.gitignore` (다국적 + IDE), `LICENSE`
- **deploy_target_kind**: `local`, compose_path 미설정
- **옵션**: 없음
- **용도**: "내 스택은 내가 가져온다" — README + 기본 설정만 깔끔하게

### 3.2 `fullstack_fastapi_nextjs` — Full-stack
- **스택**: FastAPI (Python 3.12) + Next.js 15 (App Router) + nginx
- **레이아웃**:
  ```
  /backend
    app/main.py, app/__init__.py
    requirements.txt
    Dockerfile
  /frontend
    app/page.tsx, app/layout.tsx
    package.json
    Dockerfile
    next.config.mjs
  /nginx
    nginx.conf
    Dockerfile
  docker-compose.yml
  ```
- **compose**: 3 서비스 (backend:8000, frontend:3000, nginx:80) + `/api/*` → backend, `/` → frontend 의 nginx 라우팅
- **deploy_target_config**: `compose_path=docker-compose.yml`, `primary_service=nginx`, `primary_port=80`, `preview_mode=subdomain`
- **옵션**:
  - `primary_port` (default 80) — nginx external port
  - `database` (Literal["none", "postgres"]) — postgres 선택 시 4번째 서비스 + alembic 셋업

### 3.3 `backend_fastapi` — Backend only
- **스택**: FastAPI + asyncpg + alembic + Postgres
- **레이아웃**:
  ```
  app/main.py, app/db.py, app/models.py
  alembic/, alembic.ini
  requirements.txt
  Dockerfile
  docker-compose.yml
  ```
- **compose**: 2 서비스 (backend:8000, postgres:5432)
- **deploy_target_config**: `compose_path=docker-compose.yml`, `primary_service=backend`, `primary_port=8000`, `preview_mode=path`, `strip_prefix=true`
- **옵션**:
  - `primary_port` (default 8000)
  - `db_name` (default `app`)

### 3.4 `frontend_nextjs` — Frontend SPA
- **스택**: Next.js 15 (standalone build) + Dockerfile
- **레이아웃**:
  ```
  app/page.tsx, app/layout.tsx
  package.json
  Dockerfile
  docker-compose.yml
  ```
- **compose**: 1 서비스 (frontend:3000)
- **deploy_target_config**: `compose_path=docker-compose.yml`, `primary_service=frontend`, `primary_port=3000`, `preview_mode=path`, `strip_prefix=false`, `upstream_scheme=`(empty 로 manifest 와 baseline 일치)
- **옵션**:
  - `primary_port` (default 3000)
  - `with_tailwind` (default true)

### 3.5 `static_vite` — Static site
- **스택**: Vite + Vanilla TS/HTML + nginx (built dist 서빙)
- **레이아웃**:
  ```
  src/main.ts, src/style.css
  index.html
  package.json
  vite.config.ts
  nginx/nginx.conf
  Dockerfile  (multi-stage: node build → nginx)
  docker-compose.yml
  ```
- **compose**: 1 서비스 (static:80)
- **deploy_target_config**: `compose_path=docker-compose.yml`, `primary_service=static`, `primary_port=80`, `preview_mode=path`
- **옵션**:
  - `primary_port` (default 80)

## 4. 아키텍처

### 4.1 디렉토리 구조

```
server/src/gapt_server/domains/scaffolds/
  __init__.py
  registry.py          # ScaffoldPreset / ScaffoldOption / 등록 + lookup
  context.py           # RenderContext (project_name, github_owner, options dict)
  github_client.py     # thin GitHub REST 래퍼 (verify_token, get_user, check_repo, create_repo, delete_repo)
  pusher.py            # git init + commit + push 헬퍼 (subprocess + asyncio)
  errors.py            # ScaffoldError + 코드 enum
  presets/
    __init__.py        # `ALL_PRESETS: list[ScaffoldPreset]` 등록
    empty.py
    fullstack_fastapi_nextjs.py
    backend_fastapi.py
    frontend_nextjs.py
    static_vite.py
```

서버 라우터:
- `server/src/gapt_server/routers/scaffolds.py` (신규)
  - `GET /_gapt/api/scaffolds` — 프리셋 목록 + option_schema
  - `POST /_gapt/api/projects/scaffold` — 전체 생성 트랜잭션

프론트엔드:
- `web/src/api/scaffolds.ts` (신규) — listScaffolds, createProjectFromScaffold
- `web/src/routes/ImportProjectModal.tsx` (rename: 현재 `NewProjectModal.tsx`)
- `web/src/routes/NewProjectScaffoldModal.tsx` (신규) — 위저드
- `web/src/routes/ProjectsIndex.tsx` 의 헤더 버튼 클러스터 — `[새로고침] [프로젝트 불러오기] [+ 새 프로젝트]`

### 4.2 GitHub 토큰 해결 순서 (사용자 답변 R1 반영)

**Primary**: `vault` 의 `scope="system"`, `owner_id="admin"`, `key_name="github_token"`
시크릿. 사용자가 Settings → Credentials → "GitHub Personal Access Token" 에 입력해서
저장한 값. 미설정 시 → 412 `github.token_missing` + Settings 페이지 링크.

**Secondary (legacy)**: `settings.host_github_token` (gh auth token discovery) —
vault 에 없을 때만 폴백. 후방 호환용.

토큰 scope 검증:
- `GET https://api.github.com/user` → 200 + 응답 헤더의 `X-OAuth-Scopes` 에 `repo`
  또는 `public_repo` 포함되는지 체크. 미포함 시 412 `github.token_scope_insufficient`.
- fine-grained PAT (X-OAuth-Scopes 헤더 없음) → 친절한 메시지로 거절,
  "classic PAT with `repo` scope" 권장. v1 은 classic 만 지원.

읽기 패턴: `routers/ci.py` 의 `vault.list_secrets(scope="system", key_name="github_token")` →
`vault.read(secret_id, purpose="scaffold.create_repo", actor_id=user.id)` 그대로 차용.

### 4.3 GitHub API 클라이언트 (얇은 래퍼)

```python
class GithubClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None): ...
    async def get_user(self) -> dict  # GET /user
    async def get_scopes(self) -> set[str]  # X-OAuth-Scopes 헤더 파싱
    async def repo_exists(self, owner: str, name: str) -> bool  # GET /repos/{}/{}
    async def create_repo(
        self, *, name: str, private: bool, description: str, auto_init: bool = False
    ) -> dict  # POST /user/repos — 응답에 default_branch / clone_url 포함
    async def delete_repo(self, owner: str, name: str) -> None  # DELETE /repos/{}/{} (rollback 용)
```

`auto_init=False` 이유: GitHub 의 auto_init 는 자체 README 를 만들지만, 우리는
프리셋이 만든 README 를 푸시할 거라 빈 레포가 더 깔끔합니다.

### 4.4 Scaffold 푸시

```python
async def push_scaffold(
    *, repo_clone_url: str, token: str, files: dict[str, bytes], commit_message: str
) -> str:  # 반환: 첫 커밋 SHA
    """tempdir 에 git init → 파일 쓰기 → add → commit → remote add (token 포함 URL) → push"""
```

토큰 임베드 URL: `https://x-access-token:<TOKEN>@github.com/<owner>/<repo>.git` —
HTTPS 기본 채널. `Authorization: bearer` 헤더보단 URL 임베드가 git push 쪽에서
의존성 적음.

브랜치 이름: GitHub 의 새 레포 default 가 `main` 이므로 우리도 `main` 으로 푸시.
사용자 계정이 default branch 를 `master` 로 설정해뒀다면? `create_repo` 응답의
`default_branch` 를 보고 거기로 푸시.

커밋 작성자: GAPT 가 만든 시스템 커밋이므로 `GAPT scaffold <gapt@hrletsgo.me>`.
나중 커밋은 GAPT 에이전트의 정상 git config 로 진행.

### 4.5 전체 트랜잭션 (`POST /projects/scaffold`)

```
1. validate body (slug 형식, repo_name 형식, preset_id 등록 여부, options 스키마)
2. resolve token (host_github_token 우선)
3. GithubClient.get_scopes() — `repo` 포함 확인
4. GithubClient.get_user() — owner 얻기
5. GithubClient.repo_exists(owner, repo_name) — 이미 있으면 409
6. 프리셋.render(ctx) → files: dict[str, bytes]
7. GithubClient.create_repo(name, private, description) → repo_info
8. push_scaffold(repo_info.clone_url, token, files, "Initial scaffold from GAPT")
   * 실패 시: delete_repo 시도 후 500 (rollback)
9. DB 트랜잭션:
   a. projects insert (git_remote_url=repo_info.html_url, git_provider="github",
      default_compose_paths=[preset.compose_path] 등)
   b. (옵션) workspaces insert + 초기 워크스페이스 클론 task enqueue
10. audit row (scaffold.create)
11. 응답 반환
```

push (8) 와 DB (9) 사이가 best-effort 이긴 한데, push 까지 성공했으면 GitHub
상태가 권위 있는 source-of-truth 라서 DB 실패 시에도 레포는 삭제하지 않습니다
(operator 가 수동 import 로 복구 가능).

### 4.6 API 계약

```http
GET /_gapt/api/scaffolds
→ 200 {
    "presets": [
      {
        "id": "fullstack_fastapi_nextjs",
        "display_name": "Full-stack (FastAPI + Next.js)",
        "description": "백엔드 / 프론트엔드 / 리버스 프록시가 한 번에",
        "stack": ["FastAPI", "Next.js 15", "nginx", "Docker Compose"],
        "icon": "stack",
        "option_schema": [
          { "id": "primary_port", "type": "integer", "default": 80, "label": "외부 포트" },
          { "id": "database", "type": "enum", "choices": ["none", "postgres"], "default": "none", "label": "데이터베이스" }
        ]
      },
      ...
    ]
  }
```

```http
POST /_gapt/api/projects/scaffold
{
  "slug": "my-app",
  "display_name": "My App",
  "repo_name": "my-app",
  "repo_visibility": "private",        // private | public
  "preset_id": "fullstack_fastapi_nextjs",
  "preset_options": { "primary_port": 80, "database": "postgres" },
  "create_initial_workspace": true
}
→ 201 {
  "project": ProjectResponse,
  "repo": {
    "name": "my-app",
    "html_url": "https://github.com/cocorof/my-app",
    "clone_url": "https://github.com/cocorof/my-app.git",
    "default_branch": "main"
  },
  "scaffold_summary": {
    "files_created": 14,
    "commit_sha": "abc1234..."
  },
  "workspace": WorkspaceResponse | null
}
→ 409 { code: "github.repo_exists", reason: "..." }
→ 412 { code: "github.token_missing", reason: "..." }
→ 412 { code: "github.token_scope_insufficient", reason: "needs `repo` scope, has [`gist`, `read:org`]" }
→ 500 { code: "github.create_failed", reason: "..." }
→ 500 { code: "scaffold.push_failed", reason: "..." }
```

### 4.7 데이터 모델 변경

신규 컬럼:
- `projects.scaffold_preset_id` (TEXT, nullable) — 어떤 프리셋으로 만들어졌는지 audit 용

마이그레이션 1건 (alembic). NULL 가능하므로 기존 row 영향 없음.

## 5. 프론트엔드 UX

### 5.1 ProjectsIndex 헤더 (사용자 답변 반영: 드롭다운)

기존: `[새로고침] [+ 새 프로젝트]`
신규: `[새로고침] [+ 새 프로젝트 ▾]` — 클릭 시 메뉴:
- **새로 만들기** → `NewProjectScaffoldModal` (위저드)
- **불러오기** → `ImportProjectModal` (기존 `NewProjectModal` 이름 변경)

split-button 이 아니라 일반 dropdown menu — 사용자 mental model 이 "새 프로젝트" 라는
하나의 액션 안에서 두 가지 시작 방법을 고르는 것에 가까움.

### 5.2 위저드 — `NewProjectScaffoldModal`

4 단계:

**Step 1 — 식별 정보**
- Display Name (한글 OK, ex: "내 블로그")
- Slug (display name 에서 자동 추출, 수정 가능, kebab-case 강제)
- GitHub Repo Name (slug 기본, 수정 가능, GitHub 명명규칙 강제)
- Visibility 토글 (Private / Public, 기본 Private)

**Step 2 — 프리셋 선택**
- 5 개 카드 그리드 (icon + 이름 + 짧은 설명 + stack 칩들)
- 카드 클릭 = 선택, 다음 버튼 활성화
- 카드 hover 시 stack 항목 풀 표시

**Step 3 — 옵션 설정** (프리셋이 옵션 없으면 자동 skip)
- `option_schema` 기반 동적 폼
- integer 는 number input, enum 은 select, boolean 은 toggle

**Step 4 — 확인 + 생성**
- 요약 표시 (어디에 어떤 레포가 어떤 스택으로 만들어질지)
- "create_initial_workspace" 체크박스 (기본 true)
- "만들기" 버튼 → POST `/projects/scaffold` → 성공 시 모달 닫고 새 프로젝트
  카드로 스크롤 + toast "✓ my-app repo 가 GitHub 에 생성되었습니다"

**에러 처리**:
- 412 `github.token_*` → 메시지 + "Settings → GitHub 에서 토큰을 등록하세요" 링크
- 409 `github.repo_exists` → Step 1 으로 되돌아가서 repo_name 필드에 인라인 에러
- 500 → 토스트 + 모달 유지 (재시도 가능하게)

## 6. 단계별 분해 (Sub-phases)

| ID | 범위 | 의존 | 예상 |
|---|---|---|---|
| N.2.1 | GithubClient + token resolver + scope verify + 8 테스트 | — | 1 cycle |
| N.2.2 | ScaffoldPreset registry + RenderContext + 빈 preset + listing endpoint | N.2.1 | 0.5 cycle |
| N.2.3 | 4 종 프리셋 작성 (full-stack, backend, frontend, static) + 스냅샷 테스트 | N.2.2 | 1.5 cycle |
| N.2.4 | `pusher.py` git push 헬퍼 + 로컬 bare 레포 테스트 | N.2.1 | 0.5 cycle |
| N.2.5 | `POST /projects/scaffold` 전체 트랜잭션 + alembic migration (`scaffold_preset_id` 컬럼) + 통합 테스트 | N.2.1–4 | 1 cycle |
| N.2.6 | 프론트: `ImportProjectModal` rename + `NewProjectScaffoldModal` 위저드 + listScaffolds 호출 | N.2.5 | 1.5 cycle |
| N.2.7 | 라이브 검증 + drift 정리 + 진척 노트 | All | 0.5 cycle |

**총 ~6.5 cycle** — Phase M (9 sub-phase) 와 비슷한 규모.

## 7. 위험 + 미확정 사항

- **R1**: `gh auth token` 으로 발견된 토큰이 fine-grained PAT 일 수도 있어요. fine-grained 의 scope 표현이 classic 과 다릅니다 (`X-OAuth-Scopes` 헤더 없음, 대신 권한 매트릭스). v1 은 classic + `repo` scope 가정 — fine-grained 면 명확히 에러 메시지.
- **R2**: GitHub 가 새 레포 default branch 를 사용자 계정 설정 따라 `master` 로 만들 수 있음. `create_repo` 응답의 `default_branch` 를 신뢰하고 그쪽으로 푸시.
- **R3**: `git push` 가 OS 의 git 바이너리에 의존. dev 컨테이너 / prod 모두 git 있는지 entrypoint 에서 확인.
- **R4**: rate limit. v1 은 별다른 백오프 없이 GitHub 응답 그대로 사용자에게 노출.
- **Q1**: 프로젝트 slug 와 GitHub repo name 이 같아야 하나요? 다르게 허용? **제안**: 다르게 허용 — slug 는 GAPT 내부 URL 용, repo name 은 GitHub URL 용. 사용자가 둘 다 따로 지정.
- **Q2**: scaffold 가 다른 GAPT 사용자의 워크스페이스에서도 보여야 하나요? 단일 admin 가정이므로 무관.
- **Q3**: 프리셋의 옵션을 어디에 저장? 프로젝트 생성 후엔 마치 사용자가 직접 만든 것처럼 다루므로, 옵션은 audit log + `scaffold_preset_id` 컬럼만 기록하고 그 이후엔 GAPT 가 신경 쓰지 않음.

## 8. 검증 체크리스트

- [ ] 빈 프리셋으로 새 프로젝트 → 레포 생성됨, README 만 포함, GAPT 워크스페이스 열림
- [ ] full-stack 프리셋 → 3 서비스 docker-compose 가 워크스페이스에서 `up -d` 로 뜨고 nginx :80 으로 frontend / `/api/*` 로 backend 가 응답
- [ ] backend 프리셋 → postgres 까지 함께 떠서 alembic upgrade head 가 통과
- [ ] frontend 프리셋 → `npm run dev` 가 워크스페이스 안에서 돌고 port detector 가 :3000 잡음
- [ ] static 프리셋 → `npm run build && nginx` 가 정적 파일 서빙
- [ ] 토큰 없음 → 412 + 친절한 메시지 + Settings 링크
- [ ] 레포 이름 중복 → 409 + Step 1 으로 복귀
- [ ] git push 실패 → 레포는 삭제, 사용자에 명확한 메시지
- [ ] "프로젝트 불러오기" (기존 import) 가 회귀 없이 동작
- [ ] 새 컬럼 `scaffold_preset_id` 가 import 경로에선 NULL 로 남음

---

## 검토 후 진행 절차

이 plan 을 사용자가 검토한 후:

1. 프리셋 카탈로그 조정 (추가/제외/우선순위)
2. 위저드 UX 단계 변경 요청 반영
3. 위험/미확정 항목 의사결정 (R1, Q1 등)
4. 확정되면 `docs/progress/m2_phase_n.md` 작성 + `00_master_plan.md` 인덱스 등록
5. N.2.1 부터 순차 실행, 매 sub-phase 완료 시 보고
