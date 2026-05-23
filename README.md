# geny-adapted-project-toolkit (GAPT)

> **셀프호스트 AI DevOps 플랫폼.** 내 서버에 띄우는 `OpenHands × Coolify` 합본 + `Cursor`-급 라이브 편집 UI. 외부 Git 레포를 `git clone` → **Docker(Sysbox) 격리** → **Claude Code 기반 LLM**으로 편집·테스트·빌드·배포까지 **하나의 웹 콘솔**에서 끝낸다.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-M1--E4_complete_(beta)-yellow)](docs/11_roadmap.md)

> **M1-E4 완료 (2026-05-24).** 350+ server tests · 89+ web tests · 운영자 self-host 가능 (`compose/docker-compose.prod.yml`). 다음: 사용자가 GAPT 를 GAPT 로 dogfood + Geny 첫 어댑트 — 운영 가이드는 [`docs/operations/install.md`](docs/operations/install.md), [`docs/operations/geny-adapt.md`](docs/operations/geny-adapt.md).

---

## 한 줄 정의

```
사용자 → 브라우저 한 탭 → 좌측 프로젝트 트리(여러 외부 레포) → 워크스페이스 진입
   → Sysbox 격리 컨테이너 + SeaweedFS 영속 파일 + Claude Code CLI
   → 채팅 / 에디터 / 터미널 / 프리뷰 / CI / 배포까지 같은 세션
```

자세한 비전: [`docs/00_overview.md`](docs/00_overview.md)

---

## 왜 만드는가

2026년 5월 현재, 다음을 *동시에* 제공하는 단일 제품은 사실상 존재하지 않는다.

| 요건 | 가장 가까운 후보 | 우리와의 갭 |
|---|---|---|
| 셀프호스트 (자기 서버에 띄움) | Coolify, OpenHands, Coder | OpenHands는 *코드 에이전트*만, Coolify는 *배포*만 |
| AI 코드 에이전트 (Claude Code급) | Cursor, Windsurf | SaaS-only |
| 멀티 프로젝트 (여러 외부 레포 동시) | GitLab Duo | GitLab 안에서만 |
| 내장 CI/CD + 배포 | GitHub Actions, Coolify | 코드 에이전트와 분리됨 |
| IDE-like 라이브 편집 UI | Cursor, v0 | 셀프호스트 불가 또는 벤더 종속 |

자세한 시장 분석: [`docs/01_market_landscape.md`](docs/01_market_landscape.md)

---

## 핵심 원칙

1. **Isolation by Default** — 호스트 docker 소켓을 *어떤 모드에서도* 노출하지 않음. Sysbox runtime 1차.
2. **External Repo is First-Class** — 사용자가 이미 가진 GitHub/Gitea 레포가 일급 시민.
3. **No Vendor Lock-in** — LLM/Git 호스트/IDP/시크릿 저장소 모두 교체 가능한 어댑터.
4. **Self-Host or Get Out** — 코어는 단일 노드에서 100% 동작. SaaS 부가 서비스는 옵션.
5. **Reuse Battle-Tested Components** — Sysbox, Caddy, Monaco, dockview, xterm, geny-executor 조립.
6. **Live Edit > Batch Generate** — Cursor 식 작은 변경의 즉시 반영.
7. **Audit Everything** — 모든 LLM 호출·도구·파일 편집·배포 명령은 감사 로그.
8. **Policy by Default, Not by Code** — 위험 액션은 *PolicyEngine의 strict 기본값*. 사용자가 책임지고 config로 완화.
9. **Single Agent Backend, Manifest-driven** — `geny-executor 2.1.0+` 한곳. **`claude_code_cli` provider + host MCP wrap**으로 GAPT 도구를 CLI에 노출.

자세한 원칙: [`docs/00_overview.md`](docs/00_overview.md) §0.8

---

## 문서 구조

### 분석 (12편 — Phase 0의 산출물)

| # | 제목 | 한 줄 |
|---|---|---|
| 00 | [개요](docs/00_overview.md) | 비전, 포지셔닝, 핵심 가치, 비-목표, 용어집 |
| 01 | [시장 풍경](docs/01_market_landscape.md) | 12+ 경쟁 제품, 빈자리, 차용/회피 패턴 |
| 02 | [유스케이스/페르소나](docs/02_use_cases_and_personas.md) | P1~P4, 5 골든패스, 비-시나리오 |
| 03 | [시스템 아키텍처](docs/03_system_architecture.md) | 컨트롤/실행 플레인, 8 도메인, 데이터 흐름 |
| 04 | [LLM 에이전트 레이어](docs/04_llm_agent_layer.md) | geny-executor manifest-driven, MCP 2 boundary |
| 05 | [Git 워크플로](docs/05_git_workflow.md) | clone/worktree/PR/credential |
| 06 | [격리/런타임](docs/06_isolation_and_runtime.md) | Sysbox, Compose, 리소스, 네트워크 |
| 07 | [CI/CD/프리뷰](docs/07_cicd_and_preview.md) | inner/outer loop, DeployTarget, Caddy |
| 08 | [Web IDE UX](docs/08_web_ide_ux.md) | Monaco+dockview, 채팅 1급, Plan/Act |
| 09 | [보안/권한/감사/관측](docs/09_security_authz_observability.md) | RBAC, Vault, Audit, PolicyEngine, OTel |
| 10 | [기술 스택 결정](docs/10_tech_stack_decisions.md) | 결정 매트릭스 + 라이선스 함정 |
| 11 | [로드맵](docs/11_roadmap.md) | M0~M5 단계 + 비-목표 해제 |
| 12 | [Geny 케이스 스터디](docs/12_geny_case_study.md) | 첫 어댑트 9 step + 함정 |

### 계획 / 진행

- [`docs/plan/`](docs/plan/) — 마스터 플랜 + M0/M1 디테일 카드 + M2~M5 윤곽
- [`docs/progress/`](docs/progress/) — cycle별 진행 기록 (PR 단위 갱신)

cadence 규칙: [`docs/plan/00_master_plan.md`](docs/plan/00_master_plan.md) §0.1

---

## 코드 레이아웃 (M1 종료 시점 목표)

```
geny-adapted-project-toolkit/
├── docs/                   # 분석 + plan + progress
├── analysis/               # 신규 주제 심층 분석 (cycle 도중 자유 추가)
├── compose/                # 자체 배포 compose (dev / prod)
├── server/                 # 컨트롤 플레인 (Python + FastAPI)
├── runtime/                # gapt/runtime 컨테이너 이미지 + daemon
├── web/                    # 프론트엔드 (Vite + React)
├── caddy/                  # Caddy 템플릿
├── poc/                    # M0 PoC artifacts
└── scripts/                # 사용자/관리 스크립트
```

자세한 트리: [`docs/plan/00_master_plan.md`](docs/plan/00_master_plan.md) §0.8

---

## 의존 자원 (1차)

- **`geny-executor` 2.1.0+** (PyPI) — 에이전트 엔진
- **Claude Code CLI** (`claude`) — 1차 LLM backend
- **Sysbox runc** — 컨테이너 격리
- **SeaweedFS** — 영속 파일 코어 (host FS는 *캐시만*)
- **PostgreSQL 16+**, **Redis 7+**, **Caddy 2+**

자세한 결정 근거: [`docs/10_tech_stack_decisions.md`](docs/10_tech_stack_decisions.md)

---

## 시작하기

### Self-host (M1-E4 — production-ready single VPS)

```bash
git clone https://github.com/CocoRoF/geny-adapted-project-toolkit.git
cd geny-adapted-project-toolkit/compose

# Write `.env` with the required secrets (random 32-char each):
#   GAPT_DOMAIN, GAPT_PREVIEW_DOMAIN, ACME_EMAIL,
#   GAPT_POSTGRES_PASSWORD, GAPT_SESSION_SECRET,
#   GAPT_DAEMON_JWT_SECRET, GAPT_VAULT_MASTER_KEY,
#   GAPT_SHARE_LINK_SECRET, GAPT_SEAWEED_SECRET_KEY
# Full list + .env template in docs/operations/install.md §2.

# Optionally enable Prometheus + Grafana with --profile metrics.
docker compose -f docker-compose.prod.yml --profile metrics up -d
```

Then open `https://<GAPT_DOMAIN>/`, sign in via magic link, and follow
[`docs/operations/install.md`](docs/operations/install.md) §6 to
register GAPT itself as a GAPT project (dogfood).

### Adopting an external repo (Geny case)

[`docs/operations/geny-adapt.md`](docs/operations/geny-adapt.md) walks
through registering Geny, wiring the multi-file compose chain, loading
secrets, and running the first PR cycle from inside GAPT.

### Local dev

```bash
# Boot dependencies (Postgres / Redis / SeaweedFS / Caddy):
docker compose -f compose/docker-compose.dev.yml up -d --wait

# Run the FastAPI server with the local DB:
cd server && uv run uvicorn gapt_server.app:app --reload --port 8001

# Vite dev server on the web UI:
cd web && pnpm dev   # → http://localhost:5173

# Server tests (needs the dev Postgres up):
GAPT_TEST_POSTGRES_DSN="postgresql://gapt:gapt_dev_only@localhost:5432/gapt" \
  uv run pytest
```

---

## 라이선스

[Apache License 2.0](LICENSE).

코어는 *영구 OSS*. 클라우드 부가 서비스(M5 이후 옵션)는 별도 라인이지만 코어 OSS를 잠그지 않는다.

---

## 기여

[`CONTRIBUTING.md`](CONTRIBUTING.md). 모든 PR은 *plan/progress 카드를 참조*해야 한다 — cadence 규칙은 거기에.

---

## 관련 프로젝트

- [geny-executor](https://github.com/CocoRoF/geny-executor) — GAPT가 의존하는 21단계 에이전트 파이프라인 (Apache-2.0)
- [Geny](https://github.com/CocoRoF/Geny) — GAPT의 *첫 어댑트 사례* (별개 호스트)
