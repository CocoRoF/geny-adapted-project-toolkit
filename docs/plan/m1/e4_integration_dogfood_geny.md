# M1-E4: 통합 — Deploy / Audit Dashboard / Policy override / Dogfood / Geny 첫 어댑트

> Status: planned
> Estimated: 10 작업일 / 12 PR
> Depends on: M1-E1, M1-E2, M1-E3
> Blocks: M2 진입
> Relates to: [`../../07_cicd_and_preview.md`](../../07_cicd_and_preview.md), [`../../09_security_authz_observability.md`](../../09_security_authz_observability.md), [`../../11_roadmap.md`](../../11_roadmap.md) §11.3 DoD, [`../../12_geny_case_study.md`](../../12_geny_case_study.md)

## 목적 (한 줄)
앞 3개 epic의 토대를 *실제 가치 흐름*으로 묶는다 — 사용자가 변경을 prod에 배포하고, 비용/감사를 대시보드에서 보고, 정책을 자기 책임으로 완화하고, **GAPT가 GAPT 자신을 유지보수하고(dogfood)**, **Geny 한 사이클을 외부 IDE 없이 완수**한다.

## 진입 조건
- [ ] M1-E1, M1-E2, M1-E3 모두 통과
- [ ] 사용자가 *자기 prod 서버* 정의 (예: VPS의 SSH 키 등록)
- [ ] 사용자가 *Geny 레포 접근* GitHub OAuth 통과
- [ ] [`12`](../../12_geny_case_study.md) (전체) 일독
- [ ] [`07`](../../07_cicd_and_preview.md) §7.4 (DeployTarget) + §7.5 (2-Factor) 일독

## DoD (Definition of Done — M1 전체의 게이트)
- [ ] `LocalComposeTarget` + `RemoteSshTarget` + `WebhookTarget` 동작
- [ ] prod 배포는 2FA TOTP 필수 (PolicyEngine 기본)
- [ ] CI 결과 (GitHub Actions polling) 라이브 표시
- [ ] **PolicyEngine config override 시스템** — server / org / project 4계층 병합 + 사용자 UI에서 편집 + audit
- [ ] **Audit dashboard** — Grafana 가능하면 Grafana, 아니면 자체 UI (시간대 / scope / action 필터, 일별 / 모델별 비용 그래프)
- [ ] OTel SDK 통합 + Prometheus exporter (호스트 메트릭 + GenAI semantic conventions)
- [ ] **🎯 Dogfood 통과**: GAPT를 GAPT에서 유지보수 — 본 레포의 다음 PR을 GAPT 워크스페이스에서 만들어서 머지하고 prod (GAPT 자체) 재배포 성공
- [ ] **🎯 Geny 첫 어댑트 통과**: Geny v0.20.0 cycle 작업 한 사이클을 외부 IDE 0번 사용으로 완수 (plan → analysis → implement → test → PR → main 머지 → prod 배포)
- [ ] M1 전체의 [11_roadmap §11.3 DoD](../../11_roadmap.md) 모두 체크

## 작업 항목 (세부)

### Cycle 4.1 — DeployTarget 어댑터 3종 (2 PR)
- `gapt_server/domains/deploy/`:
  - `DeployTarget` Protocol (07 §7.4.2 시그너처)
  - `LocalComposeTarget` — 호스트의 *다른* Sysbox 컨테이너 (`gapt-prod-{project}`)에 compose up
  - `RemoteSshTarget` — 등록된 SSH 키로 원격 호스트에 compose 실행, 단명 ssh-agent
  - `WebhookTarget` — HMAC 서명된 POST
- 각 target의 `deploy(ctx) / status / rollback` 메서드
- 시크릿 단명 주입 + 사용 후 zeroize 검증

### Cycle 4.2 — Build/Deploy Orchestrator (D6) + Deploy API (1 PR)
- `gapt_server/domains/deploy/orchestrator.py`:
  - `POST /api/environments/{env_id}/deploy {confirm_2fa?}` →
    1. PolicyEngine `deploy.{env_name}` 평가
    2. `REQUIRE_2FA`면 TOTP 검증 (없으면 412)
    3. Secret Vault에서 환경 secret read (단명)
    4. DeployTarget 어댑터 호출
    5. 진행 로그 SSE
    6. 종료 audit (성공/실패)
- `POST /api/environments/{env_id}/rollback {to_version}`
- 직전 5분 내 진행 중인 deploy 있으면 queue + 사용자 확인

### Cycle 4.3 — CI 결과 polling + UI 통합 (1 PR)
- M1-E2의 `GithubProvider.list_workflow_runs` + `get_workflow_run_logs` 활용
- 백그라운드 ARQ: 활성 워크스페이스의 PR 브랜치에 대해 10s polling
- `WS /api/projects/{pid}/ci/stream` → 진행 + 결과
- CI 그린 → 채팅에 자동 메시지 ("CI passed. Ready to merge?")
- Webhook 옵션 (사용자 호스트가 외부 도달 가능한 경우): `POST /api/integrations/github/webhook` (HMAC 검증)

### Cycle 4.4 — Caddy subdomain 동적 등록 (1 PR)
- Caddy admin API (`POST /config/...`) 동적 갱신
- 워크스페이스 생성 → `{slug}.preview.{domain}` 등록 + on-demand TLS
- 사용자 SSO 인증 게이트 (M1-E1 세션 cookie 검증) — 기본
- 공유 토글: HMAC 서명된 share URL 생성 (`/api/workspaces/{wid}/share?ttl=...`)

### Cycle 4.5 — PolicyEngine config 시스템 — 4계층 (2 PR)
- 4계층 모델 ([09](../../09_security_authz_observability.md) §9.2.3):
  - L1 Built-in default bundle (코드, 변경 X)
  - L2 Server-wide overrides (`/etc/gapt/policies.yaml` + 핫 리로드)
  - L3 Org overrides (DB `org_policies` 테이블)
  - L4 Project overrides (DB `project_policies` + `.gapt/policy.yaml` 옵션)
- 병합 알고리즘: 아래 계층이 위 계층을 *명시적으로* override (각 액션 단위)
- API:
  - `GET /api/policies?scope={server|org|project}/{id}` — 효과 정책 보기 (병합 결과 + 어느 계층 출처인지)
  - `PUT /api/policies/{scope}/{id}` — 변경 (owner 권한 + 확인 모달 + audit)
- 변경 시 *현재 효과* vs *변경 후 효과* diff UI
- 완화 변경(deny → allow 류)은 추가 확인 모달
- 5개 코드 강제 불변식 (§9.2.4)은 *PUT API에서 거부*

### Cycle 4.6 — Audit Dashboard (1 PR)
- `/audit` 라우트:
  - 시간대 (오늘/어제/일주일/사용자 지정)
  - scope (전체 / 프로젝트 / 환경 / 세션)
  - action 필터 (multi-select, 자주 사용은 chip)
  - outcome 필터 (ok / error / denied / masked)
  - exec_code 검색 (정확 일치 또는 prefix)
  - 결과 테이블 + 페이징
  - 1 클릭으로 페이로드 상세 (마스킹된 평문 제외)
- CSV/JSONL 내보내기
- *모든* 페이로드는 시크릿 마스킹 (정규식 통과 검증)

### Cycle 4.7 — 비용 대시보드 + OTel Prometheus (2 PR)
- `/cost` 라우트:
  - 일별/월별 사용량 (라인 차트)
  - 프로젝트별 (스택)
  - 모델별 (파이)
  - cap 설정 + 현재 진행률 (게이지)
- `gapt_server/observability/`:
  - OTel SDK init (`gen_ai.*` semantic conventions)
  - Prometheus exporter (`/metrics`)
  - 메트릭: `gapt_sessions_active`, `gapt_sandbox_count{status}`, `gen_ai.usage.input_tokens{model,project}`, `gen_ai.cost_usd_total`
- Grafana dashboard JSON 1개 ship (`compose/grafana/dashboards/gapt-overview.json`)
- compose.dev.yml에 grafana + prometheus 추가 (옵션 profile)

### Cycle 4.8 — 알림 (1 PR)
- 알림 채널:
  - UI 토스트 + 알림 패널 (기본)
  - Slack/Discord webhook (사용자 등록)
  - (옵션) 이메일 (SMTP 설정 있는 경우)
- 트리거:
  - Deploy 완료/실패
  - CI 그린/실패
  - cost cap 80% 도달
  - 정책 거부 (사용자가 LLM에게 시킨 작업이 deny된 경우 알림)
- 사용자별 알림 설정 페이지

### Cycle 4.9 — 헤드리스 API + 단일 액션 트리거 (1 PR)
- `POST /api/sessions/oneshot {project_id, env_id?, message}` → 세션 생성 + 메시지 1개 + 완료 대기 → 결과 JSON
- Project-scoped API token 인증 (`agent.run` 권한)
- M5의 cron/webhook용 인터페이스 *형태만* 미리. cron 스케줄러 UI는 M5.

### Cycle 4.10 — Dogfood: GAPT에 GAPT 등록 (1 PR)
- 본 레포(`geny-adapted-project-toolkit`)를 GAPT 자체에 프로젝트로 등록
- `compose/docker-compose.dev.yml` + `compose/docker-compose.prod.yml` 정비
- 사용자 자체 운영 환경 정의:
  - `dev`: 로컬 호스트 다른 sandbox에 compose up
  - `prod`: 원격 VPS에 RemoteSshTarget로
- GAPT의 다음 PR을 *GAPT 워크스페이스에서* 작성 → CI 그린 → 머지 → prod 재배포
- progress에 *실제 사이클 로그* 기록 (검증)

### Cycle 4.11 — Geny 첫 어댑트 (M1 마지막 게이트) (1 PR)
- [`12`](../../12_geny_case_study.md) Step 1~9 그대로 수행
- 추가 GAPT 도구 필요 시 *executor에 PR* 후 받음 ([[feedback_extend_executor_not_adapter_layer]])
- 실제 cycle 작업물 (analysis/plan/progress 갱신 + Geny 코드 변경) 머지
- 사용자 검증: *데스크탑 Cursor 0회 사용 완수* + 비용 ≤ 일 cap + prod 배포 성공
- `analysis/2026XXXX_geny_first_adapt_lessons.md`에 학습 정리

### Cycle 4.12 — M1 종합 검증 + 사용자 검토 (1 PR)
- [`11`](../../11_roadmap.md) §11.3 DoD 5개 모두 체크
- 격리 검증 9개 시나리오 자동 재실행 (회귀 확인)
- 메모리 누수/자원 누수 1주일 soak 테스트
- README + CONTRIBUTING + 운영 가이드 (`docs/operations/install.md`) 작성
- 데모 영상 (3분 골든패스)

## 산출물
```
server/src/gapt_server/
├── domains/deploy/{target.py, local.py, ssh.py, webhook.py, orchestrator.py}
├── domains/ci/{poller.py, github_webhook.py}
├── policy/
│   ├── config_loader.py             # 4계층 병합
│   ├── server_yaml.py
│   └── project_yaml.py
├── observability/
│   ├── otel.py
│   └── prometheus.py
├── routers/
│   ├── deploy.py
│   ├── policies.py
│   ├── audit_query.py
│   ├── notifications.py
│   ├── headless.py
│   └── ci.py
└── caddy/{admin_api.py, subdomain.py}

web/src/
├── deploy/{DeployModal.tsx, DeployHistory.tsx}
├── policies/{PolicyEditor.tsx, PolicyDiff.tsx}
├── audit/{AuditDashboard.tsx}
├── cost/{CostDashboard.tsx}
└── notifications/{NotificationCenter.tsx, NotificationSettings.tsx}

compose/grafana/dashboards/gapt-overview.json
compose/prometheus/prometheus.yml

docs/operations/
├── install.md
├── upgrade.md
├── runbook.md                       # 사고 대응 절차
└── policy_examples.yaml             # 일반적 완화 정책 예시

analysis/
├── 2026XXXX_geny_first_adapt_lessons.md
└── 2026XXXX_m1_retrospective.md
```

## 검증 시나리오
1. **Dogfood**: GAPT 워크스페이스에서 본 레포의 README 한 줄 수정 → commit → PR → CI 그린 → 머지 → prod 재배포 → 새 페이지 응답에 변경 반영. *외부 도구 0회.*
2. **Geny 첫 어댑트**: Geny 레포로 같은 흐름. plan/progress 갱신 포함. 비용 ≤ $5.
3. **PolicyEngine 4계층 검증**:
   - server `/etc/gapt/policies.yaml`에 `deploy.prod: REQUIRE_2FA` (default와 동일) → UI 표시 일치
   - project `.gapt/policy.yaml`에 `deploy.prod: REQUIRE_USER_APPROVAL` (완화) → owner 확인 모달 → 적용 → audit `policy.change` 기록
   - 그 후 LLM이 deploy.prod 도구 호출 → 사용자 클릭 1회로 통과 (2FA 불요)
4. **deploy 실패 시나리오**: 잘못된 SSH 키 → RemoteSshTarget fail → `exec.unknown` 또는 별도 코드 → 사용자에게 명확한 에러 + rollback 안내 (자동 X).
5. **비용 cap 도달**: 사용자가 의도적으로 작은 cap → 80% 도달 알림 → 100% 도달 시 deny + 모달.
6. **헤드리스 oneshot**: `curl -X POST .../sessions/oneshot ...` → JSON 응답 + audit 기록.
7. **알림**: Slack webhook 등록 → deploy 성공/실패 메시지 도착.
8. **격리 회귀**: M0-P2 9개 시나리오 재실행 → 모두 그린.
9. **soak**: 1주일 동안 GAPT 자체 운영 + 매일 5+ 세션 → 메모리/CPU 안정.
10. 데모 영상 3분: 사용자 진입 → 첫 PR 머지 → 배포 → 비용 표시까지.

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|
| **Geny 어댑트 시 발견되지 못한 빠진 도구** (예: `gapt_run_tests`) | 큼 — M1 게이트 미통과 | M1-E2에서 도구 추가 시 *executor에 PR* — GAPT는 의존 버전 올림. plan 카드를 새로 만들지 않고 cycle 4.11 안에서 처리 |
| Dogfood 첫 사이클이 *GAPT 자체 버그*로 깨짐 | 큼 | 격리 강도 덕에 *서비스 중단 없이* 디버그. Sandbox 안에서 우선, prod 배포는 검증 후 |
| Caddy admin API 동적 변경이 인증서 발급 폭주 (Let's Encrypt rate limit) | 중 | on-demand TLS의 `ask` 엔드포인트로 *등록된 워크스페이스만* 발급 허용, M1까지는 자체 서명도 옵션 |
| Audit 데이터가 *민감 정보 누출* (페이로드에 사용자 입력 평문) | 큼 | 마스킹 정규식 + audit 페이로드 *기본 hash only*, 평문은 별도 hot table (30일) |
| PolicyEngine config UI가 너무 복잡 → 사용자가 *직접 YAML 편집*만 함 | 중 | YAML editor (Monaco) + 미리보기 diff. UI는 *완화 흐름 단순화*에 집중 |
| OTel 메트릭이 컨테이너별 cardinality 폭증 | 중 | `project_id` 등 label은 화이트리스트, 자유 텍스트 label 금지 |
| Geny v0.21.0 cycle이 *우리 일정과 충돌* (Geny 작업이 GAPT 의존을 만들면 데드락) | 중 | Geny 어댑트는 *기존 cycle을 GAPT로 옮기는* 정도. 신규 Geny cycle을 GAPT에 의존시키지 않는다 |
| 데모 영상 녹화 = 사용자 시간 부담 | 작음 | M1 종료 후, 사용자 자율 |

## 관련 docs
- [`../../07_cicd_and_preview.md`](../../07_cicd_and_preview.md) §7.2 inner loop, §7.4 DeployTarget, §7.5 2FA, §7.7 프리뷰
- [`../../09_security_authz_observability.md`](../../09_security_authz_observability.md) §9.2.3 PolicyEngine 4계층, §9.4 Audit, §9.5 Observability
- [`../../11_roadmap.md`](../../11_roadmap.md) §11.3 M1 DoD
- [`../../12_geny_case_study.md`](../../12_geny_case_study.md) — Step 1~9 + 함정 G-1~G-8
- [[feedback_durable_instructions]], [[feedback_policy_config_not_hardcode]]
