# M0-P1: 모노레포 셋업 + CI

> Status: planned
> Estimated: 3 작업일 / 4 PR
> Depends on: (없음 — 최초 cycle)
> Blocks: M0-P2, M0-P3 (모든 후속 작업)
> Relates to: [`../../10_tech_stack_decisions.md`](../../10_tech_stack_decisions.md), [`../../11_roadmap.md`](../../11_roadmap.md) §11.2

## 목적 (한 줄)
GAPT의 *모든 코드 추가가 동일한 빌드/테스트/품질 게이트를 거치도록* 모노레포 + CI 파이프라인을 셋업한다.

## 진입 조건
- [x] 12편 분석 docs 통과
- [ ] GitHub 레포 생성 (예: `gkfua00/geny-adapted-project-toolkit`)
- [ ] GitHub Actions 활성화
- [ ] 개발자 머신에 `uv` (0.4+), `pnpm` (9+), `docker` (24+) 설치

## DoD (Definition of Done)
- [ ] `server/`, `runtime/`, `web/` 각각 빈 패키지 초기화 + 빌드 통과 (hello-world 수준)
- [ ] GitHub Actions: lint + type-check + test 매트릭스 그린
- [ ] `compose/docker-compose.dev.yml`로 백엔드/SeaweedFS/Postgres/Redis가 *부팅 + 헬스체크* 통과 (실 기능 없어도 좋음)
- [ ] README.md + LICENSE (Apache-2.0) + CONTRIBUTING.md 셋팅
- [ ] `pre-commit` 훅: ruff (Python), eslint+prettier (TS), markdownlint
- [ ] PR 템플릿에 *plan/progress 참조 필드* 포함

## 작업 항목 (세부)

### 1. 레포 부팅
- `git init` + `git remote add origin ...`
- Apache-2.0 LICENSE
- `.gitignore` (Python/Node/IDE/OS 표준 + `.gapt/secrets`)
- `.editorconfig`
- `README.md` — 한 줄 포지셔닝 + 12 docs 링크 + plan 링크
- `CONTRIBUTING.md` — cadence 규칙 ([`../00_master_plan.md`](../00_master_plan.md) §0.1) 요약

### 2. `server/` 셋업 (Python + uv)
- `server/pyproject.toml` — Python 3.12, deps: `fastapi`, `uvicorn[standard]`, `arq`, `redis`, `sqlalchemy[asyncio]`, `psycopg[binary]`, `alembic`, `pydantic-settings`, `geny-executor>=2.1.0`, `httpx`, `structlog`, `opentelemetry-sdk`
- dev deps: `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `httpx[testing]`
- `server/src/gapt_server/__init__.py` + `app.py` (hello-world FastAPI)
- `server/src/gapt_server/settings.py` (pydantic-settings)
- `server/tests/test_health.py` — `/health` 엔드포인트 테스트

### 3. `runtime/` 셋업 (컨테이너 이미지 + daemon)
- `runtime/Dockerfile` — Debian bookworm-slim 베이스, git/gh/docker CLI/python3.12/uv/node 22/build-essential 포함 ([06](../../06_isolation_and_runtime.md) §6.6)
- `runtime/pyproject.toml` — `toolkit-agent` 패키지, deps: `aiohttp`, `pyjwt`, `geny-executor>=2.1.0` (옵션, 데몬 안에 executor 직접 부르는 경우 위해)
- `runtime/src/gapt_runtime/daemon.py` — 빈 stdio + unix socket 서버 (다음 cycle에서 채움)
- `runtime/tests/test_daemon_smoke.py`

### 4. `web/` 셋업 (Vite + React + Tailwind + shadcn/ui base)
- `web/package.json` — pnpm 워크스페이스, deps: `react@19`, `react-dom`, `vite@6`, `tailwindcss@3`, `@radix-ui/*`, `lucide-react`, `react-router-dom@7`, dev: `@vitejs/plugin-react`, `vitest`, `eslint`, `prettier`
- `web/src/main.tsx` + `App.tsx` + `index.css` (Tailwind base + CSS vars 토큰)
- `web/src/i18n/` — `en.ts`, `ko.ts` (빈 catalog, `exec.*.*` 키만 미리 등록)
- `web/tests/smoke.test.tsx`

### 5. `compose/` — 개발용 부팅 스택
- `compose/docker-compose.dev.yml`:
  - `postgres:16` — port 5432, healthcheck
  - `redis:7` — port 6379, AOF on
  - `seaweedfs/seaweedfs:3.x` (`server -filer -s3` 단일 노드 모드) — port 9333(master) / 8888(filer) / 8333(s3)
  - `caddy:2` — port 80/443, 자동 HTTPS off (dev 자체 서명)
  - `gapt-server`: `build: ../server`
  - `gapt-web`: `build: ../web` (또는 `vite dev` 서비스)
- `compose/seaweed/` — Filer config(`filer.toml`), replication policy 등
- `compose/caddy/Caddyfile.dev` — 단순 reverse proxy
- 헬스체크 + `depends_on: condition: service_healthy` 체인 명시

### 6. CI — GitHub Actions
- `.github/workflows/ci.yml`:
  - 매트릭스: `python-server` / `python-runtime` / `node-web`
  - Python: `uv sync && uv run ruff check && uv run mypy && uv run pytest`
  - Node: `pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test`
  - 캐시: `actions/cache`로 `uv`/`pnpm` 캐시
- `.github/workflows/compose-smoke.yml`:
  - `docker compose -f compose/docker-compose.dev.yml up -d --wait`
  - `curl http://localhost:.../health` 5개 서비스 응답 확인
- `.github/PULL_REQUEST_TEMPLATE.md`:
  - `Plan: docs/plan/...` 필수 필드
  - `Progress: docs/progress/...` 필수 필드
  - 체크리스트: lint/test/manifest 영향 / docs 갱신 필요 여부

### 7. pre-commit + 품질 도구
- `.pre-commit-config.yaml`:
  - `ruff` (server, runtime)
  - `eslint --fix`, `prettier --write` (web)
  - `markdownlint` (docs)
  - `check-yaml`, `check-toml`, `trailing-whitespace`, `end-of-file-fixer`
- `pyproject.toml` 양측에 ruff/mypy 공통 설정 (`tool.ruff`, `tool.mypy`)
- `web/.eslintrc.cjs` + `web/.prettierrc.cjs`

## 산출물 (예상 파일 트리)
```
geny-adapted-project-toolkit/
├── .editorconfig
├── .gitignore
├── .pre-commit-config.yaml
├── LICENSE
├── README.md
├── CONTRIBUTING.md
├── .github/
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/
│       ├── ci.yml
│       └── compose-smoke.yml
├── compose/
│   ├── docker-compose.dev.yml
│   ├── caddy/Caddyfile.dev
│   └── seaweed/filer.toml
├── server/
│   ├── pyproject.toml
│   ├── src/gapt_server/{__init__.py,app.py,settings.py}
│   └── tests/test_health.py
├── runtime/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── src/gapt_runtime/{__init__.py,daemon.py}
│   └── tests/test_daemon_smoke.py
└── web/
    ├── package.json
    ├── pnpm-lock.yaml
    ├── vite.config.ts
    ├── tsconfig.json
    ├── src/{main.tsx,App.tsx,index.css,i18n/{en.ts,ko.ts}}
    └── tests/smoke.test.tsx
```

## 검증 시나리오
1. `docker compose -f compose/docker-compose.dev.yml up -d --wait` 실행 → 5개 서비스 모두 `healthy`.
2. `curl http://localhost:8080/health` → `{"status": "ok", "version": "0.0.1"}`.
3. `curl http://localhost:9333/cluster/status` → SeaweedFS master 응답.
4. `pnpm dev` (web) → 브라우저에서 hello-world 페이지 표시.
5. PR 1개 생성 → CI 3개 job 모두 그린 + compose-smoke job 그린.

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|
| Sysbox 없이 SeaweedFS 부팅이 user namespace 충돌 | M0-P2 진입 전 발견되면 큼 | P1에선 *순수 Docker*로 SeaweedFS 부팅 (격리 강도 무관). P2에서 Sysbox runtime으로 이전 |
| GitHub Actions 무료 분 한도 | 빌드 빈도 폭증 시 청구 | compose-smoke는 PR에만, push에는 lint+test만 |
| pnpm workspace 셋업 거침 | 시간 손실 | 단일 `web/` 패키지 우선, 멀티 패키지는 M2 이후 |
| Caddy 자체 서명 인증서가 브라우저 경고 | dev UX 거침 | dev에선 plain HTTP 허용 옵션, 사용자에게 가이드 |

## 관련 docs
- [`../../10_tech_stack_decisions.md`](../../10_tech_stack_decisions.md) §10.1 — 매트릭스 (Python/Vite/Postgres/SeaweedFS 결정)
- [`../../06_isolation_and_runtime.md`](../../06_isolation_and_runtime.md) §6.6 — runtime 이미지 베이스
- [`../../11_roadmap.md`](../../11_roadmap.md) §11.2 — M0 DoD
