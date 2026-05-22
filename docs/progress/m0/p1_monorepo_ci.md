# M0-P1: 모노레포 셋업 + CI — 진행 기록

> Plan: [`../../plan/m0/p1_monorepo_ci.md`](../../plan/m0/p1_monorepo_ci.md)
> Status: **in_progress**
> Started: 2026-05-22
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
### PR 5 — compose/ dev 스택 (대기)
### PR 6 — CI workflows (대기)
### PR 7 — pre-commit + 품질 도구 (대기)

## DoD 진행

- [ ] `server/`, `runtime/`, `web/` 빈 패키지 빌드 통과
- [ ] GitHub Actions: lint + type-check + test 그린
- [ ] `compose/docker-compose.dev.yml` 부팅 + 5 서비스 헬스체크 통과
- [ ] README + LICENSE + CONTRIBUTING
- [ ] pre-commit 훅 활성
- [ ] PR 템플릿 (plan/progress 참조 필드)

## Drift (cycle 종료 시 작성)

*(아직 종료되지 않음)*
