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
### Cycle 1.2 — 설정 / DI 컨테이너 (✅ 완료 — *this commit*)

- `settings.py` 확장 — Cycle 1.7+ 에 쓰일 키들 추가: `sandbox_runtime` (`sysbox-runc`), `sandbox_image_tag`, `sandbox_daemon_socket`, `sandbox_daemon_token_ttl_s`, `sandbox_idle_pause_s` (30min), `sandbox_idle_archive_s` (24h), `arq_queue_name`, `audit_flush_interval_s` / `audit_max_batch_size`, `request_id_header`.
- `container.py` — `AppContainer` 데이터클래스 (settings / engine / session_factory) + `build_container(settings)` 가 `postgres_dsn` 없으면 engine None 으로 booted (테스트 친화), `_coerce_async_dsn` 가 `postgresql://` / `+asyncpg://` 모두 `+psycopg://` 로 정규화. FastAPI `Depends` 3종: `get_container` / `get_app_settings` / `get_db_session` (async generator).
- `middleware/trace_id.py` — `TraceIdMiddleware` 가 매 요청마다:
  - 들어오는 `X-Request-Id` 검증 (8~80자, printable ASCII) → 통과 시 echo, 실패/없으면 ULID 생성
  - `structlog.contextvars.bind_contextvars(trace_id, method, path)` → 핸들러에서 emit 되는 모든 로그가 trace_id 포함
  - 응답 헤더에 trace_id 첨부, 요청 종료 시 contextvars clear (worker re-use 누수 방지)
- `app.py` 와이어업 — `create_app(settings, container)` 두 인자, lifespan 이 `container.aclose()` 호출하여 engine dispose, 미들웨어 ordering: TraceId 가 *최외곽* (CORS 보다 먼저) 등록되어 모든 후속 미들웨어 로그도 trace_id 보유.
- 테스트 추가:
  - `test_trace_id.py` (4 tests) — generated ULID, echo, malformed 거부, **structlog 출력에서 trace_id 발견**
  - `test_container.py` (6 tests) — DSN coercion 3종, postgres 없을 때도 부팅 가능, postgres 있으면 engine 생성/dispose, `get_db_session` 에러 메시지, `get_app_settings` Depends, `get_container` fallback path
- 결과: 18 PASS (이전 8 → +10), coverage 97%, ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **`opentelemetry` 미사용**: pyproject 에 의존성은 있으나 본 cycle 에서는 structlog 만으로 trace_id 처리. OTel exporter 와이어업은 Cycle 1.12 (관측 통합) 또는 후속 PR 로 이월.
- **per-request `AsyncSession` factory 만 노출**: `get_db_session` 가 async generator yield 패턴. 별도 transactional middleware 는 추가 안 함 — endpoint 가 명시적 `session.commit()` 호출 (도메인 서비스 패턴 일관성).
### Cycle 1.3 — D2 Auth: MagicLink IDP (✅ 완료 — *this commit*)

- `gapt_server/domains/auth/` 신규 패키지:
  - `session.py` — `TokenStore` + `SessionStore` Protocols + 인메모리 구현 (asyncio.Lock 으로 동시 접근 안전, TTL 기반 자동 만료). Redis-backed 구현은 후속 cycle 에서 wire-up.
  - `idp.py` — `AuthIdp` Protocol + `MagicLinkIdp` (token-by-email, one-shot consume, 첫 사용자 OWNER 자동 승격 + `default` org 자동 생성). `MagicLinkDelivery` Protocol 로 SMTP/콘솔 swap 가능 (M1 dev = `ConsoleDelivery`, 로그에 callback URL 출력). `build_memory_idp()` 헬퍼.
- `routers/auth.py` — 4 endpoints:
  - `POST /api/auth/magic-link` (202, EmailStr 검증)
  - `GET /api/auth/magic-link/callback?token=` → 토큰 소비, user/org 시드, session cookie 발급
  - `POST /api/auth/logout` (204 + cookie clear)
  - `GET /api/auth/me` (인증 검증 + 사용자 반환)
  - `get_current_user` Depends — 다른 라우터가 인증 강제 시 재사용
  - `_DEFAULT_IDP` module-level singleton + `set_auth_idp` 훅 (테스트 / 부팅 시 swap)
- `app.py` 가 auth 라우터 include
- `email-validator>=2.2` 의존성 추가 (pydantic `EmailStr`)
- 테스트 5개 (`tests/auth/test_magic_link_flow.py`):
  - 전체 플로우 happy-path (magic-link → callback → cookie → /me → token replay 401)
  - 잘못된 토큰 → 401
  - cookie 없이 /me → `auth.session.missing` 401
  - logout → 204 + cookie 클리어 → /me 401
  - 두 번째 사용자 등록 → 첫 번째와 다른 user_id
  - **pytest-asyncio fixture** 패턴으로 fixture teardown 시 `container.aclose()` 가 engine dispose — 이전 테스트의 unraisable ResourceWarning 누수 방지
- 결과: 23 PASS (auth 5 + 누적 18), ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **Redis 미사용**: plan §1.3 는 "Redis 세션 store" 명시. 본 cycle 은 in-memory 만 ship — Redis 와이어업은 Redis 의존 도입 시점 (Cycle 1.7 sandbox 가 ARQ 큐로 Redis 쓰기 시작) 까지 연기. 모든 store 가 Protocol 뒤에 있어서 swap-in 비용 거의 0.
- **SMTP delivery 미와이어**: plan 은 "SMTP 발송 (개발은 콘솔 출력 모드)". 본 cycle 은 `ConsoleDelivery` 만 — SMTP 어댑터는 SMTP 의존 도입 시 추가, `MagicLinkDelivery` Protocol 인터페이스는 이미 안정.
- **세션 cookie `secure` 가드**: `settings.env != "dev"` 일 때만 secure flag. prod 에서는 강제. plan 명시 안 됐지만 [09](../../09_security_authz_observability.md) §9.2 의 cookie hardening 일관성.
### Cycle 1.4 — D7 Secret Vault (✅ 완료 — *this commit*)

- `gapt_server/domains/secrets/`:
  - `backend.py` — `SecretBackend` Protocol + `EncryptedSqliteBackend` (Fernet, PBKDF2-HMAC-SHA256 480k iter, WAL SQLite). `SecretRef` opaque handle (backend:locator 형식, 평문은 backend 외부에서 절대 비공개).
  - `vault.py` — `SecretVault` — `store/read/rotate/delete/list/get_metadata`. `SecretMetadata` 데이터클래스 (평문 없음). 모든 `read` 는 `secret.read` 이벤트 emit (Cycle 1.5 의 AuditSink 가 hook into 예정). IntegrityError → `secret.duplicate` 코드, 누수된 ciphertext 자동 cleanup.
- `routers/secrets.py` — 5 endpoints (POST/GET/GET id/POST rotate/DELETE) 모두 `get_current_user` Depends. 응답 schema (`SecretView`) 는 **value 필드 자체가 없음** — 평문이 wire 에 절대 안 실림.
- `app.py` 가 router include
- `cryptography>=43.0` 의존성 추가
- 설정 추가: `vault_master_key` (PBKDF2 입력, env override 강제), `vault_sqlite_path` (default `.gapt/local/vault.sqlite3`).
- 테스트 10개:
  - `test_vault.py` (7 tests): backend round-trip + 디스크에 평문 없음 검증, foreign ref 거부, SecretRef 파싱, vault store/read/delete cycle + Postgres backend_ref 에 평문 없음 검증, duplicate key 거부, rotate 가 old blob 제거 + 평문 부재 확인, fuzz pattern `GAPT-FUZZ-TOKEN` 디스크 grep 미발견.
  - `test_routes.py` (3 tests): auth gate, HTTP 전체 lifecycle (response 에 value 부재 확인 + plaintext 가 list 응답 텍스트에 부재), 409 duplicate.
- 결과: 33 PASS (이전 23 → +10), ruff + mypy strict 그린, coverage 99%.

#### Plan 카드 대비 변경

- **OS keyring backend 미구현**: plan §1.4 가 `OsKeyringBackend` 1차 + `EncryptedSqliteBackend` fallback. 본 cycle 은 EncryptedSqlite 만 ship — `keyring` 패키지가 헤드리스 컨테이너에서 디폴트 백엔드 없음 + dev/CI 가 일관되게 동작 + Fernet 가 단일 노드 위협 모델에 충분. `SecretBackend` Protocol 뒤에 있어서 keyring 추가 비용 거의 0.
- **audit_events 직접 기록 미연결**: plan §1.4 "모든 read는 audit 이벤트 `secret.read` 발행". 본 cycle 은 structlog 로 emit. Cycle 1.5 가 `AuditSink` 구현하면 vault 가 hook into 함 — Protocol 인자로 inject 예정 (현재 구조에 swap 거의 0).
### Cycle 1.5 — D8 Audit Sink (✅ 완료 — *this commit*)

- `gapt_server/domains/audit/`:
  - `masking.py` — `scrub()` regex-based 평문 시크릿 redaction. 7 패턴 (Anthropic / OpenAI old/new / GitHub PAT / GitHub fine-grained / Slack / Bearer). dict/list 재귀.
  - `sink.py` — `AuditSink` Protocol + 3 구현:
    - `NullAuditSink` (test 친화 noop)
    - `InMemoryAuditSink` (test assertion 용)
    - `PostgresAuditSink` (asyncio.Queue → background flush task → batched INSERT). `start()` / `aclose()` lifecycle, `max_queue_size`/`max_batch_size`/`flush_interval_s` 튜닝. Queue 가득 차면 drop + warning (caller 절대 block 안 함).
    - 모든 row 가 `masking.scrub` 통과 후 insert — 평문 secret 이 `audit_events.payload` 에 도달 불가.
  - `AuditAction` 상수 (action 문자열 안정성)
- `container.py` 가 `AuditSink` 를 1급 의존성으로 hold, `app.lifespan` 이 `PostgresAuditSink.start()` 호출, `aclose()` 가 drain.
- `routers/secrets.py` `get_vault` 가 audit_sink Depends 로 주입 → `SecretVault.read` 가 `secret.read` 이벤트 emit
- 테스트 12개:
  - `test_masking.py` (6 tests): 7 패턴 검증 + dict/list 재귀 + 비-string passthrough
  - `test_sink.py` (5 tests): Null/InMemory 동작, 200 이벤트 burst 가 모두 보존 + ULID 정렬, secret 패턴이 INSERT 전 scrub, queue-full drop 동작
- 결과: 45 PASS (이전 33 → +12), ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **Redis Streams 미사용**: plan §1.5 가 "Redis Streams로 비동기 flush". 본 cycle 은 in-process `asyncio.Queue`. Redis 의존 도입 (Cycle 1.7 ARQ) 시 swap-in. Protocol 뒤에 있어서 caller 변경 없음.
- **월 파티션 미준비**: plan 의 audit_events monthly partition 은 Cycle 1.1 Drift 에서 이미 deferred. 본 cycle 은 non-partitioned table 에 batch insert — partition 추가 시 INSERT path 변경 없음 (Postgres declarative partitioning 가 INSERT 를 partition 으로 라우팅).
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
