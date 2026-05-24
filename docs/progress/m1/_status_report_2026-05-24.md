# GAPT 진행 상태 리포트 — 2026-05-24

> 사용자 요청: 지금까지 어느정도 진행됐는지 + 어떻게 테스트할지 아주 자세하게.
> 본 문서는 *지금 이 순간의 스냅샷* 이며, 앞으로 사용자가 self-host / dogfood / Geny 어댑트를 진행하면서 함께 갱신.

---

## 1. 전체 요약 (한 페이지)

| 차원 | 현재 상태 |
|---|---|
| **마일스톤 단계** | M1 마지막 epic (E4) 까지 assistant 측 작업 완료. **M1 close 직전** — 남은 검증은 사용자 self-host + dogfood + Geny 첫 어댑트. |
| **전체 commit 수** | 87 (main 브랜치, M0 부터 누적) |
| **M1-E4 commit 수** | 19 (Cycle 4.1 ~ 4.12 + 사전 작업) |
| **server 소스** | 91 파일, ~12,573 LoC, 17 router · 12 domain |
| **server 테스트** | 50 파일, **350 case pass** · ruff + mypy clean |
| **web 소스** | 60 파일, ~5,624 LoC |
| **web 테스트** | 21 파일, **89 case pass** · typecheck + eslint + prettier + build clean |
| **운영 가이드** | [`docs/operations/install.md`](../../operations/install.md) (8 step self-host) + [`docs/operations/geny-adapt.md`](../../operations/geny-adapt.md) (Geny 어댑트 runbook) |
| **compose** | dev + **prod (신규 4.10)** + Caddy on-demand TLS + Prometheus/Grafana `--profile metrics` |
| **DoD 6 항목** | 3 ✓ · 3 부분 (사용자 단계 필요) |

**한 줄 결론**: assistant 가 할 수 있는 *모든* 코드/문서 작업은 M1-E4 까지 끝났다. 남은 것은 사용자 본인이 운영 환경(VPS)에서 dogfood 사이클을 한 번 돌리는 것.

---

## 2. M1 전체 진행 매트릭스

### M1-E1 — Backend Foundation (완료)
도메인 + Postgres + MagicLink auth + Project/Workspace CRUD + Sandbox + Audit + SecretVault + PolicyEngine 골격.

### M1-E2 — Agent + Git (완료)
geny-executor pipeline + session manager + SSE stream + GitHub Device Flow + git/PR 도구 + HookRunner (policy + audit + cost).

### M1-E3 — Web IDE Shell (완료)
React + Vite + dockview + Monaco + Chat SSE + DiffCard + ToolCallCard + CostModal + Audit/CI/Preview Panel + 4 layout preset + cmdk + i18n + PWA.

### M1-E4 — 통합 / Dogfood / Geny (완료)

| Cycle | 핵심 산출물 | 테스트 +건수 | 사용자 테스트 가능 surface |
|---|---|---|---|
| **4.1** | `DeployTarget` Protocol + LocalCompose/RemoteSsh/Webhook 3종 | server +12 | 없음 (라우터 없음) |
| **4.2** | `DeployOrchestrator` (policy → 2FA → secret → target → audit) + `POST /api/environments/:eid/{deploy,rollback}` | server +11 | curl deploy 호출 |
| **4.3** | `GET /api/projects/:pid/ci/runs` + CiPanel | server +4 · web +3 | curl + 워크스페이스 패널 |
| **4.4** | Caddy admin API client + dynamic subdomain + HMAC share link | server +18 | curl preview/share (Caddy 필요) |
| **4.5** | PolicyEngine L1+L2 (built-in + server YAML override) + invariant floors | server +13 | curl `/api/policies` |
| **4.6** | Audit dashboard CSV/JSONL export + date range + Load more | server +4 · web +2 | curl export + AuditPanel 다운로드 |
| **4.7** | 비용 dashboard endpoint + Prometheus `/metrics` (3 counter + 2 gauge) | server +13 · web +5 | curl `/api/cost/*` + `/cost` route + `/metrics` scrape |
| **4.8** | NotificationService + Slack/Discord webhook + 헤더 bell | server +9 · web +3 | curl `/api/notifications/test` + 헤더 🔔 |
| **4.9** | `POST /api/sessions/oneshot` 헤드리스 endpoint | server +7 | curl oneshot |
| **4.10** | `docker-compose.prod.yml` + Caddy on-demand TLS + Grafana dashboard + `install.md` | server +3 | 사용자 VPS 부팅 시 가능 |
| **4.11** | 다중 compose 파일 chain (`compose_paths`) + Geny adapt runbook | server +1 | Geny 어댑트 실행 |
| **4.12** | `_summary.md` + README 갱신 + DoD 체크리스트 | 코드 변경 없음 | 본 문서 자체 |

---

## 3. DoD 6 항목 상세 (`docs/11_roadmap.md` §11.3)

| # | 기준 | 상태 | 검증 방법 |
|---|---|---|---|
| 1 | 사용자가 GAPT 를 GAPT 로 유지보수 (dogfood) | **~** infra 준비 완료 | `docs/operations/install.md` §6 → 본인 PR 1개 GAPT 안에서 머지 |
| 2 | 사용자가 Geny 에서 1사이클을 외부 IDE 없이 완수 | **~** infra + runbook 준비 완료 | `docs/operations/geny-adapt.md` 7 step 완수 |
| 3 | 골든패스 G1–G4 동작 | **✓** | 본 리포트 §4–9 의 curl 시나리오 |
| 4 | 격리 시나리오 I1–I9 자동 통과 | **6/9 ✓** | `tests/sandbox/` `tests/e2e/` CI 자동 / Sysbox 실 runtime 필요한 3개는 사용자 VPS |
| 5 | LLM 비용이 세션 헤더에 라이브 표시 | **✓** | 채팅 패널 헤더 + CostModal + `/cost` route |
| 6 | 첫 토큰 latency < (API + 100ms) | **~** 계측 가능 | `/metrics` + 사용자 실측 (Anthropic API latency 의존) |

---

## 4. 사용자가 직접 테스트할 surface — 단계별 가이드

### 4.0 사전 — Postgres + 서버 부팅

```bash
# 1. Postgres + Redis + SeaweedFS + Caddy
cd /home/geny-workspace/geny-adapted-project-toolkit/compose
docker compose -f docker-compose.dev.yml up -d postgres redis seaweedfs

# 2. FastAPI 서버 (uv 필요)
cd ../server
uv run uvicorn gapt_server.app:app --reload --port 8001 &
SERVER_PID=$!

# 3. 헬스체크
curl -s http://localhost:8001/health
# → {"status":"ok","version":"...","db":true}
```

### 4.1 매직 링크 로그인 + 프로젝트/워크스페이스 생성

```bash
# 매직 링크 요청 (서버 로그에 callback URL 출력)
curl -s -X POST http://localhost:8001/api/auth/magic-link \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'

# 서버 로그에서 `auth.magic_link.console_delivery` 의 callback_url 복사
# 그 토큰으로 콜백 호출:
TOKEN="<copy>"
curl -c /tmp/c.txt -s "http://localhost:8001/api/auth/magic-link/callback?token=$TOKEN" | jq
# → {"user_id":"01K...", "org_id":"01K..."}

ORG_ID="01K..."  # 위 응답에서

# 프로젝트 생성
PROJECT_ID=$(curl -b /tmp/c.txt -s -X POST http://localhost:8001/api/projects \
  -H "Content-Type: application/json" \
  -d "{\"org_id\":\"$ORG_ID\",\"slug\":\"demo\",\"display_name\":\"Demo\",\"git_remote_url\":\"https://github.com/octocat/Hello-World.git\"}" | jq -r .id)
echo $PROJECT_ID

# 워크스페이스 생성
WID=$(curl -b /tmp/c.txt -s -X POST "http://localhost:8001/api/projects/$PROJECT_ID/workspaces" \
  -H "Content-Type: application/json" -d '{"branch":"master"}' | jq -r .id)
echo $WID
```

### 4.2 Cycle 4.2 — Deploy API

```bash
# 환경 생성 (webhook target — 외부 endpoint 없으면 503 받지만 흐름은 확인 가능)
ENV_ID=$(curl -b /tmp/c.txt -s -X POST "http://localhost:8001/api/projects/$PROJECT_ID/environments" \
  -H "Content-Type: application/json" \
  -d '{"name":"dev","deploy_target_kind":"webhook","deploy_target_config":{"webhook_url":"http://localhost:9999/hook"},"secret_refs":[]}' | jq -r .id)

# Deploy 호출
curl -b /tmp/c.txt -X POST "http://localhost:8001/api/environments/$ENV_ID/deploy" \
  -H "Content-Type: application/json" \
  -d '{"version":"v1"}'
# → 200 {"run_id":"...", "status":"success" | "failed", "exec_code":..., "log":"..."}
```

검증 포인트:
- PolicyEngine 가 `deploy.dev` action 평가 (allow by default)
- audit log 에 `deploy.start` + `deploy.success/failed` 두 줄 기록
- 4.8 의 자동 알림 발사 → 헤더 bell + Slack/Discord (설정 시)

### 4.3 Cycle 4.3 — CI runs (GitHub PAT 필요)

```bash
# PAT 환경변수로 설정 후 서버 재시작
GAPT_CI_GITHUB_TOKEN=ghp_yourtoken uv run uvicorn gapt_server.app:app --port 8001

# 실 GitHub repo URL 로 프로젝트 만든 후:
curl -b /tmp/c.txt "http://localhost:8001/api/projects/$PROJECT_ID/ci/runs?branch=main&limit=10" | jq
# → 200 [{"id":..., "name":"CI", "head_branch":"main", "status":"completed_success", ...}]
```

검증 포인트:
- 토큰 미설정 → 412 `ci.no_token` 친화 메시지
- repo URL 파싱 실패 → 412 `ci.repo_unparseable`
- 워크스페이스 화면에서 custom layout 으로 CiPanel 드래그

### 4.4 Cycle 4.4 — Caddy preview + share link

```bash
# Caddy 가 :2019 admin API 노출하고 있어야 함 (실 운영에서는 prod compose 가 처리)
export GAPT_CADDY_ADMIN_URL="http://localhost:2019"
export GAPT_CADDY_PREVIEW_DOMAIN="preview.localhost.dev"
export GAPT_SHARE_LINK_SECRET="$(openssl rand -hex 32)"
# 서버 재시작

# 프리뷰 등록
curl -b /tmp/c.txt -X POST "http://localhost:8001/api/workspaces/$WID/preview" \
  -H "Content-Type: application/json" \
  -d '{"upstream_host":"10.0.0.5","upstream_port":3000}'
# → 200 {"host":"01k....preview.localhost.dev", "workspace_id":"01K..."}

# 공유 링크 (1시간)
curl -b /tmp/c.txt -X POST "http://localhost:8001/api/workspaces/$WID/share?ttl_s=3600" | jq
# → {"token":"01K....abc...", "url":"https://...", "expires_in_s":3600}

# Caddy on-demand TLS 가드 (인증 불필요 — Caddy 호출 시뮬레이션)
curl "http://localhost:8001/api/preview/ask?domain=${WID,,}.preview.localhost.dev"
# → 200 {"domain":"..."} (워크스페이스 알려진 경우)
curl "http://localhost:8001/api/preview/ask?domain=attacker.preview.localhost.dev"
# → 404 {"detail":{"code":"preview.unknown",...}}
```

### 4.5 Cycle 4.5 — Policy YAML override

```bash
# 1. 정상 YAML
cat > /tmp/gapt-policies.yaml <<EOF
actions:
  git.push.protected:
    decision: allow
    reason: "local CI is the gate"
EOF

GAPT_POLICY_CONFIG_PATH=/tmp/gapt-policies.yaml uv run uvicorn gapt_server.app:app --port 8001

curl -b /tmp/c.txt http://localhost:8001/api/policies | jq
# → {rows: {...}, invariants: {"deploy.prod":"require_2fa",...}}

# 2. 잘못된 YAML (불변식 위반)
echo 'actions: {deploy.prod: allow}' > /tmp/bad.yaml
GAPT_POLICY_CONFIG_PATH=/tmp/bad.yaml uv run uvicorn gapt_server.app:app --port 8001
# → PolicyConfigError on startup — 서버 부팅 실패 (의도된 동작)
```

### 4.6 Cycle 4.6 — Audit export

```bash
# 채팅 / 도구 호출을 몇 번 한 후:
curl -b /tmp/c.txt \
  "http://localhost:8001/api/projects/$PROJECT_ID/audit/export?format=csv&action_prefix=agent." \
  -o audit.csv
head -3 audit.csv
# id,ts,actor_type,actor_id,action,outcome,duration_ms,exec_code,scope,subject,payload

curl -b /tmp/c.txt \
  "http://localhost:8001/api/projects/$PROJECT_ID/audit/export?format=jsonl&outcome=error" \
  -o audit.jsonl
wc -l audit.jsonl
```

웹 UI:
- 워크스페이스 → audit panel
- 시간 범위 select: "최근 30일" / "custom"
- "Export CSV" / "Export JSONL" 버튼 → 브라우저 다운로드
- 100개 이상 결과 → "Load more" 버튼 출현

### 4.7 Cycle 4.7 — 비용 dashboard + Prometheus

```bash
# 채팅 세션 한 두번 돌려서 cost 누적시킨 후:
curl -b /tmp/c.txt "http://localhost:8001/api/cost/summary?since=2026-05-01T00:00:00Z" | jq
# → {"rows":[{"project_id":"...","cost_usd":0.012,"session_count":3,...}], "total_cost_usd":...}

curl -b /tmp/c.txt "http://localhost:8001/api/projects/$PROJECT_ID/cost/daily" | jq
# → [{"date":"2026-05-20","cost_usd":0.005,...}, ...]

# Prometheus scrape (인증 불필요 — Caddyfile.prod 가 prod 에서는 외부 차단)
curl http://localhost:8001/metrics
# # TYPE gapt_sessions_active gauge
# gapt_sessions_active 0
# # TYPE gapt_agent_cost_usd_total counter
# gapt_agent_cost_usd_total{project_id="01K..."} 0.012
# # TYPE gapt_sandbox_count gauge
# gapt_sandbox_count{state="running"} 1
```

웹 UI:
- 헤더에서 직접 `/cost` 라우트 이동 (또는 워크스페이스 안에서 CostPanel 드래그)
- 범위 select 변경 (7일/30일/90일/전체) → 자동 재페치
- 프로젝트 row 클릭 → 일별 CSS 바 펼침

### 4.8 Cycle 4.8 — 알림

```bash
# (옵션) Slack/Discord 설정
export GAPT_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
export GAPT_DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
# 서버 재시작

# 테스트 알림
curl -b /tmp/c.txt -X POST http://localhost:8001/api/notifications/test \
  -H "Content-Type: application/json" \
  -d '{"title":"hello","body":"wired!","severity":"warn"}'
# → 메모리 ring + Slack + Discord 모두 도착

# 피드 확인
curl -b /tmp/c.txt http://localhost:8001/api/notifications | jq
```

웹 UI:
- 헤더 우측 🔔 → unread 배지 → 클릭 시 드롭다운
- 4.2 의 deploy 호출 후 자동 알림 발사 확인

### 4.9 Cycle 4.9 — Oneshot

```bash
# 단일 호출 (Anthropic API 키 + 워크스페이스 sandbox 필요)
curl -b /tmp/c.txt -X POST http://localhost:8001/api/sessions/oneshot \
  -H "Content-Type: application/json" \
  -d "{\"workspace_id\":\"$WID\",\"message\":\"list files in src/\",\"timeout_s\":60}"
# → 200 {
#     "session_id":"...",
#     "status":"ok",
#     "text":"src/foo.py\nsrc/bar.py\n...",
#     "tool_calls":[{...}],
#     "tool_results":[{...}],
#     "cost":{"cost_usd":0.012,...},
#     "events":[...]
#   }

# Timeout
curl -b /tmp/c.txt -X POST http://localhost:8001/api/sessions/oneshot \
  -H "Content-Type: application/json" \
  -d "{\"workspace_id\":\"$WID\",\"message\":\"do something slow\",\"timeout_s\":5}"
# → {status:"timeout",exec_code:"exec.session.timeout"}
```

검증 포인트:
- 세션은 응답 후 자동 archive (DB `status=archived`)
- audit log 에 `session.create` + `session.archive` + 도구 호출 모두 기록
- cost 가 agent_sessions 행에 반영됨

### 4.10 Cycle 4.10 — Production self-host (VPS 필요)

```bash
# 사용자 VPS 에서:
git clone https://github.com/<you>/geny-adapted-project-toolkit.git
cd geny-adapted-project-toolkit/compose

# .env 작성 (install.md §2 의 9개 시크릿)
cat > .env <<EOF
GAPT_DOMAIN=gapt.example.com
GAPT_PREVIEW_DOMAIN=preview.gapt.example.com
ACME_EMAIL=you@example.com
GAPT_POSTGRES_PASSWORD=$(openssl rand -hex 32)
GAPT_SESSION_SECRET=$(openssl rand -hex 32)
GAPT_DAEMON_JWT_SECRET=$(openssl rand -hex 32)
GAPT_VAULT_MASTER_KEY=$(openssl rand -hex 32)
GAPT_SHARE_LINK_SECRET=$(openssl rand -hex 32)
GAPT_SEAWEED_SECRET_KEY=$(openssl rand -hex 32)
# (옵션)
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
GAPT_SLACK_WEBHOOK_URL=
EOF

# 부팅
docker compose -f docker-compose.prod.yml --profile metrics up -d

# 헬스
docker compose -f docker-compose.prod.yml ps  # 모두 healthy 까지 ~30s
curl https://gapt.example.com/health

# Caddy 보안 검증 — 임의 서브도메인 → cert 거부 확인
curl -k https://attacker.preview.gapt.example.com/
# → Caddy ask endpoint 가 404 반환 → cert 미발급
```

### 4.11 Cycle 4.11 — Geny 첫 어댑트 (사용자 7 step)

`docs/operations/geny-adapt.md` 그대로 수행:

1. **프로젝트 등록**: `POST /api/projects` (slug=geny, git_remote_url=Geny repo)
2. **dev + prod env 생성**: `compose_paths=[docker-compose.yml, docker-compose.dev.yml, docker-compose.dev-core.yml]` (3-file chain)
3. **시크릿 등록**: `.env.dev`, `.env.prod`, SSH key 를 SecretVault 에
4. **워크스페이스 부팅**: Sysbox sandbox + git clone + compose up + Caddy 서브도메인
5. **도메인 시드 프롬프트**: CLAUDE.md + 컨텍스트 한 문단을 첫 채팅에
6. **실 cycle**: read → edit → test → commit → push → PR → CI → merge → deploy dev → deploy prod (2FA)
7. **lessons 파일**: `analysis/{date}_geny_first_adapt_lessons.md` 작성

검증 신호 (성공의 조건):
- [ ] 데스크탑 Cursor/VS Code 0회 사용
- [ ] 비용 ≤ 일 cap
- [ ] prod 2FA gate 가 *반드시* 동작 (`INVARIANT_FLOORS.deploy.prod=REQUIRE_2FA` 가 어떤 YAML 로도 우회 불가)
- [ ] 모든 도구 호출이 audit 에 보임, 의외 호출 없음

---

## 5. 자동 테스트 실행 방법

### Server (필수: Postgres dev DB 실행 중)

```bash
cd /home/geny-workspace/geny-adapted-project-toolkit/server

# Postgres 부팅 (위 4.0 참조)
docker compose -f ../compose/docker-compose.dev.yml up -d postgres

# 전체 350 case
GAPT_TEST_POSTGRES_DSN="postgresql://gapt:gapt_dev_only@localhost:5432/gapt" \
  uv run pytest

# 특정 영역만
uv run pytest tests/observability   # 7 unit (no DB)
uv run pytest tests/notifications/test_service.py   # 6 unit (no DB)
GAPT_TEST_POSTGRES_DSN=... uv run pytest tests/cost   # 6 integration
GAPT_TEST_POSTGRES_DSN=... uv run pytest tests/sessions/test_oneshot.py  # 7

# Static check
uv run ruff check src
uv run mypy src
```

### Web

```bash
cd /home/geny-workspace/geny-adapted-project-toolkit/web

# 의존성
pnpm install

# 89 case
pnpm test -- --run

# Static + build
pnpm typecheck
pnpm lint
pnpm format
pnpm build
```

---

## 6. 코드 위치 매핑

| 기능 | 서버 | 웹 | 테스트 |
|---|---|---|---|
| Deploy | `domains/deploy/` (protocol/local/ssh/webhook/orchestrator/two_factor) + `routers/deploy.py` | (UI 패널은 M2) | `tests/deploy/` (4 파일, 25+ case) |
| CI | `routers/ci.py` | `src/ci/CiPanel.tsx` | `tests/ci/test_routes.py`, `tests/CiPanel.test.tsx` |
| Caddy | `domains/caddy/` (admin_api/subdomain/share) + `routers/preview.py` (`router` + `ask_router`) | (등록 UI는 M2) | `tests/caddy/` (3 파일, 21 case) |
| Policy | `policy/config_loader.py` + `policy/engine.py` + `routers/policies.py` | (편집 UI는 L3/L4 후) | `tests/policy/` (3 파일, 13 case) |
| Audit | `routers/audit.py` (list + export) + `domains/audit/sink.py` | `src/audit/AuditPanel.tsx` | `tests/audit/` (3 파일, 19 case) |
| Cost | `domains/cost/service.py` + `routers/cost.py` | `src/cost/CostPanel.tsx` + `routes/Cost.tsx` | `tests/cost/test_routes.py` (6 case) |
| Metrics | `observability/{metrics,render,instruments}.py` + `routers/metrics.py` | (Grafana 가 소비) | `tests/observability/test_metrics.py` (7 case) |
| Notifications | `domains/notifications/{service,channel}.py` + `routers/notifications.py` | `src/notifications/NotificationBell.tsx` | `tests/notifications/` (9 case) |
| Oneshot | `routers/oneshot.py` | (헤드리스 — UI 없음) | `tests/sessions/test_oneshot.py` (7 case) |
| Ops | `compose/docker-compose.prod.yml` + `compose/caddy/Caddyfile.prod` + `compose/prometheus/` + `compose/grafana/` | — | `docs/operations/{install,geny-adapt}.md` |

---

## 7. 위험 + 차단 요인

| 항목 | 위험 | 완화 |
|---|---|---|
| Postgres 사용자측 부재 | 통합 테스트 6 개 그룹이 skip | `compose/docker-compose.dev.yml up -d postgres` 한 줄 |
| Anthropic API 키 부재 | oneshot / 채팅 실 동작 검증 불가 | 사용자 본인 키 + manifest 설정 |
| GitHub PAT 부재 | CI 패널이 빈 화면 | `GAPT_CI_GITHUB_TOKEN` 설정 (M1 server-wide, M2 에서 per-project SecretVault) |
| Slack/Discord URL 부재 | 알림은 메모리만 (UI bell 은 동작) | optional, 정상 |
| Sysbox 미설치 | 실 격리 컨테이너 부팅 불가 → MockSandboxBackend 만 동작 | `sandbox_use_real_docker=true` 시 Sysbox runc 필요. install.md prerequisites |
| VPS 부재 | dogfood (4.10) / Geny 어댑트 (4.11) 검증 불가 | M1 마지막 게이트 — 사용자 VPS 필요 |
| Demo 영상 부재 | M1 close 의 4번째 user-driven 항목 | 사용자 self-host 후 첫 cycle 녹화 |

---

## 8. 사용자가 다음 1주일 안에 해야 할 4가지

1. **VPS 에 self-host** (`docs/operations/install.md` §1–4) — 약 30분
2. **GAPT 를 GAPT 에 dogfood 등록** (install.md §6) — 약 30분 + 첫 PR 사이클 ~1시간
3. **Geny 첫 어댑트** (`docs/operations/geny-adapt.md` 7 step) — 약 2~4시간
4. **lessons 정리** — `analysis/20260601_geny_first_adapt_lessons.md` 작성 (반나절)

이 4가지가 끝나면 **M1 DoD 6 항목 모두 ✓**, M2 진입 가능.

---

## 9. 후속 (M2 로 deferred 된 것)

- ARQ background CI poller + SSE stream + GitHub Webhook ingress (4.3 deferred)
- PolicyEngine L3 (org DB) + L4 (project DB + `.gapt/policy.yaml`) + PUT API + diff UI (4.5 deferred)
- Audit subject before/after JSON diff viewer (4.6 deferred)
- OTel SDK auto-init + OTLP push (4.7 deferred)
- Per-user notification subscription UI + 이메일 채널 + cost cap 트리거 (4.8 deferred)
- Project-scoped API tokens for oneshot (4.9 — M5 cron 과 함께)
- SMTP magic-link delivery (4.10 deferred)
- Sysbox 실 runtime CI 자동 시나리오 3개 (4.12 — 사용자 VPS)

자세한 누적 drift: [`e4_integration_dogfood_geny.md`](e4_integration_dogfood_geny.md) "Drift" 섹션.

---

## 10. 참고 문서 한 페이지

- [`docs/00_overview.md`](../../00_overview.md) — 비전
- [`docs/11_roadmap.md`](../../11_roadmap.md) — M0~M5 단계
- [`docs/12_geny_case_study.md`](../../12_geny_case_study.md) — Geny 어댑트 케이스 스터디
- [`docs/plan/m1/e4_integration_dogfood_geny.md`](../../plan/m1/e4_integration_dogfood_geny.md) — 본 epic plan card
- [`docs/progress/m1/e4_integration_dogfood_geny.md`](e4_integration_dogfood_geny.md) — cycle 별 진행 로그
- [`docs/progress/m1/_summary.md`](_summary.md) — M1 전체 종합
- [`docs/operations/install.md`](../../operations/install.md) — self-host 절차
- [`docs/operations/geny-adapt.md`](../../operations/geny-adapt.md) — Geny 어댑트 runbook
