# M1-E1: 백엔드 토대 — 진행 기록

> Plan: [`../../plan/m1/e1_backend_foundation.md`](../../plan/m1/e1_backend_foundation.md)
> Status: **in_progress**
> Started: 2026-05-23
> Owner: gkfua00 (CocoRoF)
> Depends on: ✅ M0-P1 (`a4de305`), ✅ M0-P2 (`f468b15`), ✅ M0-P3 (`cd395ca`)

## 진입 조건 검증

- [x] M0-P1 (모노레포 + CI) 통과
- [x] M0-P2 (Sysbox + SeaweedFS PoC) 통과 — runtime image / FUSE 마운트 / 내부 dockerd 모두 검증
- [x] M0-P3 (executor + MCP bridge) 통과 — `decision_two_layer_policy.md` 가 PolicyEngine 구현 1차 근거
- [x] [`03_system_architecture.md`](../../03_system_architecture.md) §3.6 도메인 분할 일독
- [x] PostgreSQL 마이그레이션 도구: **Alembic 채택** (server/pyproject.toml 에 `alembic>=1.13` 이미 의존)

## Plan 카드 update (M0-P3 종료 시점 반영)

- M0-P3 PR4 의 `decision_two_layer_policy.md` 가 Cycle 1.10 (PolicyEngine 골격) 의 게이트 모델 근거가 됨. PolicyEngine 골격은 **CLI 내부 dispatch 가 stage 10 bypass** 한다는 사실을 전제로 설계 — Layer 1 (server-side hooks) 은 SDK provider 용, Layer 2b (MCP bridge in-process policy) 가 `claude_code_cli` 용. Cycle 1.10 은 Layer 1 만 구현하고 Layer 2b 는 M1-E2 Cycle 2.3 에서 합류.
- M0-P3 PR5 의 `_classify_cli_result` 휴리스틱 stream-path 미적용 finding — M1-E1 범위 X (M1-E2 upstream patch 큐). 단 audit sink 가 `exec.cli.protocol_error` 를 받았을 때 stderr 패턴 매칭으로 `permission_denied` 로 재분류하는 client-side 보정은 가능 (Cycle 1.5 에서 평가).

## Cycle 진행 로그

### Cycle 1.1 — DB 스키마 + Alembic (✅ 완료 — *this commit*)

- `server/src/gapt_server/db/` 5 모듈:
  - `base.py` — `DeclarativeBase` + 고정 `naming_convention` (constraint 이름 churn 방지)
  - `enums.py` — 10 strong enums (Role / GitProvider / DeployTargetKind / WorkspaceStatus / SandboxStatus / AgentSessionStatus / SecretOwnerScope / SecretBackend / AuditActorType / AuditOutcome). 모두 StrEnum, 와이어 값은 snake_case
  - `ulid.py` — `ulid_default` callable (PK 자동 생성)
  - `models.py` — 11 ORM 모델 (users / orgs / org_memberships / projects / project_memberships / environments / workspaces / sandboxes / agent_sessions / secrets / audit_events). `_pg_enum()` helper 가 `values_callable` 로 wire value 를 enum.value 로 강제 (default 는 enum.name 이라 native PG enum 이랑 안 맞음)
  - `session.py` — `create_engine` / `create_session_factory` 헬퍼
- `server/migrations/` Alembic 셋업:
  - `alembic.ini` (file_template 에 날짜 prefix)
  - `env.py` — `GAPT_POSTGRES_DSN` / `DATABASE_URL` env 우선, `postgresql://` 와 `+asyncpg` 둘 다 sync `postgresql+psycopg` 로 coerce
  - `versions/20260523_0001_init_init.py` — 손으로 작성한 0001_init. 10 enum type 생성 → 11 table 생성. workspaces ↔ sandboxes 순환 FK 는 `ALTER TABLE … ADD CONSTRAINT` 로 사후 추가
- `server/tests/db/test_migration.py` — round-trip 검증 (upgrade head → 11 table + 10 enum 확인 → downgrade base → 모두 정리 → 재 upgrade 가능). `GAPT_TEST_POSTGRES_DSN` 미설정 시 자동 skip
- `server/tests/db/test_models_smoke.py` — ORM 적재/조회 smoke (user → org → project → audit_event). enum value 매핑 + JSONB / ARRAY column 모두 확인
- CI: `.github/workflows/ci.yml` 의 `python-server` job 에 Postgres 16 service 추가, `GAPT_TEST_POSTGRES_DSN` 환경변수 주입 → 두 DB 테스트가 CI 에서도 실행
- 로컬 검증 결과:
  - `alembic upgrade head` → 11 tables + 10 enums 생성 ✅
  - `alembic downgrade base` → table/enum 모두 drop (`alembic_version` 빼고 0개) ✅
  - 재 upgrade → idempotent ✅
  - ORM round-trip → enum.value 가 wire 에 그대로 ✅
  - pytest 전체 8 PASS, coverage 98%, ruff + mypy strict 그린

#### Plan 카드 대비 변경 (Drift 의 일부 — cycle 종료 시 통합)

- **audit_events 월 파티션 deferred**: Plan §1.1 가 declarative partitioning 명시했으나 0001_init 에서는 plain 테이블로 생성. 이유: PG 의 partitioned table 은 모든 unique constraint 가 partition key 를 포함해야 하므로 `id PRIMARY KEY` 만 두려면 `(id, ts)` 복합 PK 로 바꿔야 함. M1 시점 audit 볼륨이 작아 indices 만으로 충분, 파티셔닝은 audit 볼륨 모니터링 후 0002+ migration 에서 추가. 인덱스 `ix_audit_events_ts` + `ix_audit_events_action_ts` 는 plan 의 query pattern 미리 커버.
- **users 테이블에 `password_hash` 등 auth 컬럼 없음**: Plan 의 magic-link 흐름은 Cycle 1.3 에서 처리, 이 cycle 은 schema 만. M1-E1 후속 마이그레이션에서 magic-link token / session 테이블 추가될 예정.
### Cycle 1.2 — 설정 / DI 컨테이너 (대기)
### Cycle 1.3 — D2 Auth: MagicLink IDP (대기)
### Cycle 1.4 — D7 Secret Vault (대기)
### Cycle 1.5 — D8 Audit Sink (대기)
### Cycle 1.6 — D1 Project Service CRUD (대기)
### Cycle 1.7 — D4 Sandbox Controller (Sysbox 어댑터) — 2 PR (대기)
### Cycle 1.8 — Workspace 라이프사이클 (대기)
### Cycle 1.9 — toolkit-agent 데몬 v1 — 2 PR (대기)
### Cycle 1.10 — PolicyEngine 골격 (대기)
### Cycle 1.11 — SeaweedFS 볼륨 라이프사이클 (대기)
### Cycle 1.12 — 통합 + 검증 (대기)

## DoD 진행

[Plan 카드](../../plan/m1/e1_backend_foundation.md) DoD 9개:

- [ ] `POST /api/auth/magic-link` + callback 동작 → 세션 쿠키 발급
- [ ] `POST /api/projects` (외부 git URL + compose 등록) → DB row + 좌측 트리
- [ ] `POST /api/projects/{pid}/workspaces` → SeaweedFS volume + Sysbox 컨테이너 + clone + compose up
- [ ] `POST /api/secrets` + `GET /api/secrets` — keyring backend 1차
- [ ] 모든 mutate 액션이 `audit_events` 에 ULID 정렬로 기록
- [ ] PolicyEngine 골격 + 기본 deny + built-in default bundle 1계층
- [ ] Alembic `0001_init` upgrade/downgrade 통과
- [ ] OpenAPI 자동 생성 + `gapt-web` type 동기화
- [ ] 신규 코드 mypy strict + ruff 그린, 테스트 커버리지 ≥ 70%

## Drift (cycle 종료 시 작성)

*(아직 종료되지 않음)*
