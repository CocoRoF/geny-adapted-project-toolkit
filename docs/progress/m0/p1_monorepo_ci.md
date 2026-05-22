# M0-P1: 모노레포 셋업 + CI — 진행 기록

> Plan: [`../../plan/m0/p1_monorepo_ci.md`](../../plan/m0/p1_monorepo_ci.md)
> Status: **done**
> Started: 2026-05-22
> Completed: 2026-05-22
> Owner: gkfua00 (CocoRoF)

## 진입 조건 검증

- [x] 12편 분석 docs 통과 (사용자 검토 완료)
- [x] M0-P1 plan 카드 작성 + 사용자 검토 통과 ("좋아 진입하자")
- [x] git identity 설정 — `CocoRoF <gkfua00@gmail.com>`
- [ ] GitHub 레포 생성 — *PR 1 종료 후 사용자가 직접 생성 + remote add*
- [x] `uv` 0.4+, `pnpm` 9+, `docker` 24+ — *별도 사전조건 (사용자 머신)*

## PR 진행 로그

### PR 1 — 레포 부팅 파일 (작성 완료, commit 대기)
- [x] `git init -b main` 완료
- [x] LICENSE (Apache-2.0) — 표준 텍스트, 저작권 "CocoRoF and geny-adapted-project-toolkit contributors"
- [x] `.gitignore` — Python/Node/IDE/OS/secrets/SeaweedFS 데이터 디렉토리 포함
- [x] `.editorconfig` — 기본 LF + 4 space, JS/TS/YAML/JSON은 2 space
- [x] `README.md` — 한 줄 정의 + 시장 갭 표 + 9 원칙 + 12 docs 인덱스 + 의존 자원 + Apache-2.0 + Phase 0 상태
- [x] `CONTRIBUTING.md` — cadence 규칙 9개 절 (cycle 흐름, PR 본문 필수 필드, 머지 체크, 우리가 안 하는 것 등)
- ✅ **commit 완료** (2026-05-22):
  - `8c7257a` docs: Phase 0 analysis (12 documents) — 13 files, 4827+
  - `4b98946` docs: plan + progress cadence (M0/M1 detail + M2-M5 outline) — 12 files, 2077+
  - `72dd392` chore: bootstrap repo (M0-P1 PR1) — 5 files, 675+
- *push 대기*: 사용자가 GitHub 레포 생성 + `git remote add origin ...` 후 push

### PR 2 — server/ 스켈레톤 (작성 완료, commit 대기)
- [x] `server/pyproject.toml` — Python 3.12+, FastAPI 0.115+, uvicorn, ARQ, Redis, SQLAlchemy 2.0 asyncio, Postgres psycopg, Alembic, **geny-executor 2.1.0+**, structlog, OTel, python-ulid, PyJWT. dev deps: pytest + pytest-asyncio + pytest-cov + httpx + ruff + mypy
- [x] `src/gapt_server/__init__.py` — `__version__ = "0.0.1"`
- [x] `src/gapt_server/settings.py` — pydantic-settings, env prefix `GAPT_`, SeaweedFS + claude + session/daemon secrets
- [x] `src/gapt_server/logging.py` — structlog JSON/console 토글
- [x] `src/gapt_server/app.py` — create_app 팩토리 + lifespan, CORS optional
- [x] `src/gapt_server/routers/health.py` — `/` + `/health` (200 ok, version)
- [x] `src/gapt_server/py.typed` — PEP 561 마커
- [x] `tests/conftest.py` — Settings fixture + AsyncClient via ASGITransport
- [x] `tests/test_health.py` — 3 테스트 (200 ok / root / 404)
- [x] `tests/test_settings.py` — 3 테스트 (defaults / env override / lru_cache)
- [x] `server/README.md` — 로컬 개발 명령 + 환경 변수 표
- [x] **검증 통과**: `uv sync --extra dev` OK / `ruff check` clean / `ruff format --check` clean / `mypy src` Success / `pytest` 6/6 pass / coverage 93%
- *commit 대기*: `feat(server): FastAPI skeleton with /health (M0-P1 PR2)`
### PR 3 — runtime/ 스켈레톤 (작성 완료, commit 대기)
- [x] `runtime/pyproject.toml` — Python 3.12+, aiohttp, pydantic, PyJWT, structlog, python-ulid. dev: pytest + ruff + mypy. `toolkit-agent` console script
- [x] `src/gapt_runtime/__init__.py` — `__version__`
- [x] `src/gapt_runtime/settings.py` — `DaemonSettings.from_env()` (`GAPT_AGENT_SOCKET` / `GAPT_DAEMON_TOKEN` / `GAPT_{PROJECT,WORKSPACE,SESSION}_ID` / `GAPT_WORKSPACE_ROOT`)
- [x] `src/gapt_runtime/daemon.py` — aiohttp `create_app(settings)` + `/health` + `/info`. **typed `web.AppKey`** 사용 (NotAppKeyWarning 회피)
- [x] `src/gapt_runtime/cli.py` — `toolkit-agent {version|serve}` 진입점, unix socket으로 부팅
- [x] `src/gapt_runtime/py.typed` — PEP 561
- [x] `tests/` — daemon smoke 3개, settings 2개, cli 2개 (총 7개)
- [x] `Dockerfile` (multi-stage) — Debian bookworm-slim + git/git-lfs/gh + docker-ce + docker-compose-plugin + Python 3.12 + uv 0.4.30 + Node 22 + pnpm/yarn + `toolkit-agent` 동봉. `gapt-entrypoint`가 inner dockerd 부팅 후 daemon spawn
- [x] `scripts/entrypoint.sh` — inner dockerd 헬스 대기 30s + exec
- [x] `runtime/README.md` — 빌드/실행/env vars
- [x] **검증 통과**: `uv sync --extra dev` OK / `ruff check` clean / `ruff format --check` clean / `mypy src` Success / `pytest` 7/7 pass / coverage 82% (cli `serve` 부분은 통합 테스트 영역)
- *commit 대기*: `feat(runtime): toolkit-agent daemon skeleton + sandbox Dockerfile (M0-P1 PR3)`
### PR 4 — web/ 스켈레톤 (작성 완료, commit 대기)
- [x] `package.json` — React 19 + react-router-dom 7 + Vite 6 + Vitest 3 + TypeScript 5.7 + eslint 9 (flat config, typescript-eslint typed-checking) + prettier 3 + @testing-library/react 16 + happy-dom
- [x] `tsconfig.{json,app,node}.json` — project references, strict + noUncheckedIndexedAccess + verbatimModuleSyntax + exactOptionalPropertyTypes
- [x] `vite.config.ts` — react plugin, `@/*` alias to `src/*`
- [x] `vitest.config.ts` — happy-dom 환경 + setup, vite/vitest 타입 충돌 회피 위해 분리
- [x] `eslint.config.js` — flat config + typescript-eslint typed rules
- [x] `.prettierrc.json` — 100 col, trailing comma, double quote
- [x] `index.html` — `<div id="root">` + `lang="ko"` + meta description
- [x] `src/main.tsx` — StrictMode + createRoot, root element 검증
- [x] `src/app/App.tsx` — placeholder shell (title + 언어 스위처 + repo 링크)
- [x] `src/i18n/{index,en,ko,LanguageSwitcher}.ts(x)` — `t(key, locale)`, en source of truth + ko parity, **exec.*.* 안정 식별자 12개 미리 등록**
- [x] `src/styles/index.css` — 미니멀 다크 토큰 (CSS vars, M1-E3에서 Tailwind+shadcn로 교체)
- [x] `tests/setup.ts` — jest-dom matchers
- [x] `tests/i18n.test.ts` — 키 parity + exec.* 커버리지 검증
- [x] `tests/App.test.tsx` — title/로케일 스위처/repo 링크 렌더
- [x] `web/README.md` — 명령 + i18n contract + plan/code 매핑
- [x] **검증 통과**: `pnpm install` OK / `pnpm typecheck` clean / `pnpm lint --max-warnings=0` clean / `pnpm format:check` clean / `pnpm test` 7/7 pass / `pnpm build` 198 KB (gzip 62 KB)
- *commit 대기*: `feat(web): Vite + React shell with i18n + exec code catalog (M0-P1 PR4)`
### PR 5 — compose/ dev 스택 (작성 완료, commit 대기)
- [x] `compose/docker-compose.dev.yml` — 5 services:
  - **postgres** 16-alpine + healthcheck (`pg_isready`)
  - **redis** 7-alpine AOF + healthcheck (`redis-cli ping`)
  - **seaweedfs** 3.99 단일 프로세스 (`server -filer -s3`) + healthcheck `/cluster/healthz`, 4 포트 노출 (9333/8888/8333/8080)
  - **server** (gapt/server) build from `server/Dockerfile`, depends_on healthcheck chain, env 모두 명시 (Postgres DSN / Redis DSN / SeaweedFS URLs + S3 credentials / 세션 / 데몬 시크릿)
  - **caddy** 2.10 edge :8080, dev plain HTTP
- [x] `compose/seaweed/s3.json` — dev identity `gapt-dev` + Admin/Read/Write/List/Tagging actions
- [x] `compose/caddy/Caddyfile.dev` — `/api/*` + `/health` → server:8088, placeholder root
- [x] `server/Dockerfile` — multi-stage, uv lock-based deps install, non-root `gapt:1000` user, expose 8088
- [x] `compose/README.md` — 부팅 명령, 서비스 맵, 볼륨 표, 시크릿 설명, plan 매핑
- [x] **검증 통과**: `docker compose config --quiet` 클린 / 5 services 인식 / 5 volumes 인식 / healthcheck 체인 + depends_on 명시
- 실 부팅 (이미지 pull → up → healthy)은 *사용자 측 검증 단계*로 위임 — `docker compose -f compose/docker-compose.dev.yml up -d --wait` 명령은 README에
- *commit 대기*: `feat(compose): dev stack with Postgres + Redis + SeaweedFS + Caddy (M0-P1 PR5)`
### PR 6 — CI workflows (작성 완료, commit 대기)
- [x] `.github/workflows/ci.yml` — 3 jobs (`python-server` / `python-runtime` / `node-web`), 매트릭스. uv + pnpm 캐시. concurrency cancel-in-progress. push + pull_request + workflow_dispatch 트리거. permissions read-only.
  - server: ruff check + ruff format check + mypy --strict + pytest
  - runtime: 동일 4 gate
  - web: typecheck + lint --max-warnings=0 + format:check + vitest run + build
- [x] `.github/workflows/compose-smoke.yml` — PR에서만 (path filter: compose/server/runtime/web 변경 시), `docker compose up -d --wait --wait-timeout 300` + `/health` + `/cluster/healthz` curl 검증 + 실패 시 logs --tail=200 + 무조건 down -v
- [x] `.github/PULL_REQUEST_TEMPLATE.md` — Plan/Progress 참조 필수 + 6개 체크박스 (CI / 카드 갱신 / docs 갱신 / 시크릿 / 격리 회귀 / PolicyEngine 불변식)
- [x] **검증 통과**: YAML 파싱 OK / 3 jobs 정상 / `permissions: contents: read`로 최소 권한 / `concurrency` cancel-in-progress 설정
- *commit 대기*: `ci: GitHub Actions for server/runtime/web + compose-smoke + PR template (M0-P1 PR6)`
### PR 7 — pre-commit + 품질 도구 (✅ 완료)
- [x] `.pre-commit-config.yaml` — 14 hooks (generic 9 + gitleaks + ruff×2 + prettier(web) + markdownlint + shellcheck)
- [x] `.markdownlint.json` — MD049/MD050 asterisk 일관
- [x] `.gitleaks.toml` — dev placeholder allowlist
- [x] `.github/workflows/pre-commit.yml` — 자체 워크플로 + pre-commit env cache
- [x] 부산 효과 수정:
  - server/runtime pyproject에 `[tool.ruff.lint.isort] known-first-party` 추가 → first-party import 빈 줄 보존
  - web `format`/`format:check` 패턴 확장 (`{src,tests}/**/*` + root `*.{ts,md}`)
  - compose/README.md emphasis → asterisk
- [x] 검증: `pre-commit run --all-files` 14/14 PASS, server 6/6 (93%) + runtime 7/7 (82%) + web 7/7 그린
- ✅ `3c5f1a3` chore: pre-commit hooks + gitleaks + markdownlint + shellcheck (M0-P1 PR7)

## DoD 진행

- [x] `server/`, `runtime/`, `web/` 빈 패키지 빌드 통과 — *각 디렉토리에서 로컬 빌드 + 테스트 통과 확인. CI는 사용자가 GitHub 레포 생성 + push 후 GitHub Actions에서 자동*
- [x] GitHub Actions: lint + type-check + test 그린 — *워크플로 작성 + YAML 검증 완료. 실 실행은 push 이후*
- [x] `compose/docker-compose.dev.yml` 부팅 + 5 서비스 헬스체크 통과 — *`docker compose config --quiet` 검증 + 5 services + 5 volumes 인식. 실제 `up --wait`는 compose-smoke 워크플로가 PR마다 검증, 사용자 측 1회 검증 시 README 가이드 따름*
- [x] README + LICENSE + CONTRIBUTING — *각 모듈에 README 추가 (server/runtime/web/compose), 루트 README + CONTRIBUTING + LICENSE*
- [x] pre-commit 훅 활성 — *PR7 완료. 사용자는 `uv tool install pre-commit && pre-commit install`로 활성화*
- [x] PR 템플릿 (plan/progress 참조 필드) — *`.github/PULL_REQUEST_TEMPLATE.md` 작성, Plan/Progress 두 줄 필수 + 6 체크박스*

## Commit 기록 요약

| commit | 주제 |
|---|---|
| `8c7257a` | docs: Phase 0 analysis (12 documents) |
| `4b98946` | docs: plan + progress cadence (M0/M1 detail + M2-M5 outline) |
| `72dd392` | chore: bootstrap repo (M0-P1 PR1) — LICENSE/README/CONTRIBUTING/.gitignore/.editorconfig |
| `c4292d9` | feat(server): FastAPI skeleton with /health (M0-P1 PR2) |
| `146c8b1` | feat(runtime): toolkit-agent daemon + sandbox Dockerfile (M0-P1 PR3) |
| `09a386a` | feat(web): Vite + React shell with i18n + exec code catalog (M0-P1 PR4) |
| `e4d94d0` | feat(compose): dev stack with Postgres+Redis+SeaweedFS+Caddy (M0-P1 PR5) |
| `c1696ae` | ci: GitHub Actions for server/runtime/web + compose-smoke (M0-P1 PR6) |
| `3c5f1a3` | chore: pre-commit hooks + gitleaks + markdownlint + shellcheck (M0-P1 PR7) |

## 자체 검증 (2026-05-22 push 후 보고)

GitHub push 완료 후 *호스트에서 직접* 검증한 결과:

### GitHub 측 (gh CLI)
- 워크플로 `CI`: 3 jobs 모두 success (runtime 13s / web 27s / server 16s)
- 워크플로 `pre-commit`: 14 hooks success in 56s
- 워크플로 `compose-smoke`: push에선 미실행 (path filter — 정상)
- 레포: public · main · 10 commits · 3 workflow files 모두 반영

### 로컬 ↔ CI parity 재검증
- server: ruff/format/mypy/pytest 6/6 (cov 93%) ✓
- runtime: 동일 7/7 (cov 82%) ✓
- web: typecheck/lint/format/test 7/7 + build 198 KB ✓

### compose smoke (사용자 docker 그룹 가입 후 진행)
이 검증 도중 **3개 hotfix가 필요**해서 PR8(hotfix)로 별도 commit:

1. **SeaweedFS healthcheck IPv6 함정** — BusyBox `wget`이 `localhost`를 `[::1]:9333`로 resolve해서 connection refused. SeaweedFS는 IPv4만 listen. → `127.0.0.1` 명시.
2. **server image — `uv sync`가 `/opt/venv`에 deps 미설치** — `VIRTUAL_ENV` env만 설정한 게 uv에 안 통함. → `UV_PROJECT_ENVIRONMENT=/opt/venv` 환경변수로 명시.
3. **server image — `gapt_server` 패키지가 venv에 미설치** — deps만 받고 project install 안 함 + multistage에서 editable .pth가 가리키는 경로(`/opt/gapt-server/src`)가 runtime stage에 없음. → (a) 두 단계 `uv sync` (deps → src → project install) + (b) `README.md` build context에 포함 (hatchling 요구) + (c) runtime stage가 deps stage의 `/opt/gapt-server` 디렉토리를 그대로 복사 (editable 경로 유지).
4. **caddy ↔ seaweedfs port 8080 충돌** — seaweedfs volume server (8080)를 호스트에 노출했더니 caddy edge와 충돌. → volume :8080은 *내부 전용*으로 (master가 자동 라우팅 — 호스트 노출 불필요).

수정 후 검증 통과 매트릭스:

| endpoint | 결과 |
|---|---|
| `http://127.0.0.1:8088/health` (server 직접) | `{"status":"ok","version":"0.0.1"}` ✓ |
| `http://127.0.0.1:8080/health` (caddy → server reverse proxy) | 동일 응답 — Caddy 라우팅 작동 ✓ |
| `http://127.0.0.1:9333/cluster/healthz` (SeaweedFS master) | `OK` ✓ |
| `http://127.0.0.1:8080/` (caddy 루트 placeholder) | 200 OK ✓ |
| `docker compose ps` | postgres + redis + seaweedfs + server + caddy 모두 Up (4개 healthy + caddy는 healthcheck 미정의) ✓ |

## Drift (plan ↔ 실제)

**plan 카드와 일치한 부분 (대부분)**:
- 7개 PR 단위 분할 그대로
- 의존 스택 (Python 3.12 + FastAPI / Node 22 + Vite + React / Postgres + Redis + SeaweedFS + Caddy) 그대로
- 14개 pre-commit hook 모두 활성

**plan에 없었지만 진행 중 *결정해야 했던* 부분 (drift)**:
1. **TypeScript project references**: plan에 단순히 "tsconfig.json"만 있었지만 실제로 `tsconfig.json` + `tsconfig.app.json` + `tsconfig.node.json` 3개 분리 필요 (vite/vitest config 타입 처리).
2. **Vitest v3 강제 업그레이드**: plan은 `^2.1.0`이지만 vite v6 + vitest v2 dual-install 충돌 → v3.0+로 변경. 사실상 무관한 결정이지만 명시.
3. **vitest.config.ts 별도 분리**: plan에선 vite.config 안에 `test:` 섹션. dual-Vite 타입 충돌 회피 위해 별도 파일 분리가 더 깔끔.
4. **`known-first-party`** ruff isort 설정 명시 — pre-commit 실행 중 발견. plan에 없는 디테일.
5. **gitleaks dev placeholder allowlist**: dev-only 자격증명 패턴들을 명시 화이트리스트로 둬야 hook이 통과. plan에 없는 함정.
6. **CI yaml의 path filter (compose-smoke)**: plan은 "PR에만"이었지만 *변경 path*도 필터링 — quota 절약.

**plan의 작업이 *실제로 더 작은 단위*로 분할된 부분**:
- PR 5에 `server/Dockerfile` 새로 추가 (plan은 PR3 runtime/Dockerfile만 명시). compose의 `server` 서비스가 build context를 필요로 해서.

**다음 cycle (M0-P2) 진입 전 사용자 검토 게이트** ([`docs/plan/00_master_plan.md`](../../plan/00_master_plan.md) §0.5):
- [ ] **사용자 GitHub 레포 생성 + `git remote add origin` + `git push -u origin main`** (이후 GitHub Actions 첫 실행 결과 확인)
- [ ] (선택) 사용자 머신에서 `docker compose -f compose/docker-compose.dev.yml up -d --wait` 1회 검증
- [ ] (선택) `pre-commit install`로 로컬 훅 활성화

검토 통과 후 M0-P2 (격리 + SeaweedFS PoC) 진입.
