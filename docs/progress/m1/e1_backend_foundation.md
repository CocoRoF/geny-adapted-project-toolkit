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
### Cycle 1.6 — D1 Project Service CRUD (✅ 완료 — *this commit*)

- `gapt_server/domains/projects/`:
  - `service.py` — `ProjectService` (CRUD + Environment CRUD + ownership 검증).
    - `create` 가 creator 에 OWNER `ProjectMembership` 자동 발급
    - `list_for_user` 가 membership join → 본인이 멤버인 프로젝트만 반환
    - `update` ≥ EDITOR, `archive` ≥ ADMIN, `create_environment` ≥ ADMIN
    - 역할 순서 매핑 (`VIEWER < EDITOR < ADMIN < OWNER`)
    - `ProjectError` 도메인 에러 + 안정 code (`project.not_found` / `project.forbidden` / `project.role_insufficient` / `project.slug_taken` / `environment.name_taken` / `org.forbidden`)
    - 모든 mutate 가 `AuditSink` 로 `project.create` / `project.update` / `project.archive` event emit
- `routers/projects.py` — 7 endpoints (POST/GET/GET id/PATCH/DELETE + 2 env routes), `get_current_user` Depends, slug regex 검증 (`^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$`), Project/EnvironmentResponse 가 view dataclass 에서 빌드, `_http_from_project_error` 가 code → HTTP status 매핑 (404/403/409/400)
- `app.py` 가 router include
- 테스트 4개 (`tests/projects/test_routes.py`):
  - 전체 lifecycle (create → list → get → patch → archive → archived filter + include_archived 토글) + 3개 audit 이벤트 검증
  - 중복 slug 409
  - 다른 사용자 (멤버십 없음) 의 list 에서 미노출 + 직접 GET 403
  - Environment CRUD + 중복 name 409
- 결과: 49 PASS (이전 45 → +4), ruff + mypy strict 그린

#### Plan 카드 대비 변경

- **rename `service.list` → `list_for_user`**: 메서드명이 builtin `list` 와 충돌, mypy 가 일부 어노테이션을 메서드 참조로 잘못 해석. 동일한 이름의 free function 이 SQLAlchemy mapping 에 잡히는 케이스도 회피.
- **`include_archived` query param**: plan 명시 안 됐지만 archive 가 soft-delete 의 의미를 가지므로 toggle 필요. default `false` (archived 행 미노출), `?include_archived=true` 로 활성화.
- **org 등록 별도 endpoint 미구현**: plan 은 D1 Project CRUD 만 명시. Org 는 `_ensure_user` 가 첫 로그인 시 default org 자동 생성 (Cycle 1.3). Org-level CRUD endpoint 는 M2 multi-tenant 시점에 추가.
### Cycle 1.7 — D4 Sandbox Controller (Sysbox 어댑터) — 2 PR

#### PR 1 (1.7a) — Protocol + invariants + MockSandboxBackend (✅ 완료 — *this commit*)

- `gapt_server/domains/sandbox/`:
  - `backend.py` — `SandboxBackend` Protocol (`create/start/stop/inspect/destroy/exec_in`), `SandboxCreateSpec` / `SandboxRef` / `SandboxInfo` / `SandboxResources` / `MountSpec` / `ExecResult` 데이터클래스, **`SecurityInvariantError`** 별도 예외 (config 로 약화 불가, 코드 강제), `forbidden_mount_paths()` + `validate_mounts()` — 호스트 docker socket / `/proc` / `/sys` / `.ssh` / `.aws` / `.kube` / SSH agent socket 모두 거부
  - `mock_backend.py` — `MockSandboxBackend` 상태머신 (creating → running → stopped → destroyed) + `exec_in` 의 canned response 주입 (테스트 친화), 모든 `create` 가 `validate_mounts` 통과 후에만 인스턴스화
- 테스트 26개:
  - `test_invariants.py` (23 parametrized): 17 위험 path 모두 거부 + 4 안전 path 허용 + first-violation 단락
  - `test_mock_backend.py` (4): full state machine, docker.sock mount 거부, canned exec response, destroy 후 start 거부
- 결과: 75 PASS (이전 49 → +26), ruff + mypy strict 그린.

#### PR 2 (1.7b) — SysboxBackend (real docker SDK) (✅ 완료 — *this commit*)

- `docker>=7.1` 의존성 추가
- `gapt_server/domains/sandbox/sysbox_backend.py`:
  - `SysboxBackend` — docker-py 7.x `DockerClient` 을 injectable 로 받음. `containers.create/start/stop/remove/exec_run` 를 `asyncio.to_thread` 로 async wrap.
  - `validate_mounts` 가 `create` 첫 줄에서 실행 → 위험 mount 가 docker call 까지 도달 불가
  - 모든 컨테이너에 `runtime=sysbox-runc` + base labels (`gapt.managed=true`, `gapt.runtime=sysbox-runc`, `gapt.sandbox_id`, `gapt.project_id`, `gapt.workspace_id`) + env (`GAPT_SANDBOX_ID`, `GAPT_PROJECT_ID`, `GAPT_WORKSPACE_ID`) 주입. cgroup v2 limits (`cpu_quota`, `cpu_period`, `mem_limit`, `pids_limit`) 가 `SandboxResources` 에서 매핑.
  - `wait_for_daemon(ref)` 스텁 — 컨테이너가 `running` 상태에 도달할 때까지 0.5s 폴링, `daemon_healthcheck_timeout_s` (default 60s) 초과 시 `SandboxBackendError`. Cycle 1.9 가 실제 데몬 소켓 + JWT round-trip 로 채움.
  - `make_default_client()` lazy factory — prod 부팅 경로용. 테스트는 `MagicMock` injection.
- `mypy.overrides` 에 `docker.*` 추가 (types-docker 가 stale)
- 테스트 9개 (`test_sysbox_backend.py`) — 전부 `MagicMock` 기반, CI 에 sysbox-runc 불필요:
  - sysbox runtime + GAPT env + labels 주입 검증
  - mounts → docker-py volumes shape 변환
  - `/var/run/docker.sock` mount 시도 → SecurityInvariantError + `containers.create` 호출 0회 (invariant 가 upstream gate)
  - 전체 lifecycle (start/inspect/stop/destroy) thread-hop 검증
  - exec_in demux stdout/stderr
  - wait_for_daemon polling success + timeout
  - create 예외 wrap
  - container_id=None → 명확한 에러
- 결과: 84 PASS (이전 75 → +9), ruff + mypy strict 그린.

##### Plan 카드 대비 변경

- **integration smoke 미작성**: plan §1.7 가 "실제 docker daemon 위에서 컨테이너 부팅" 를 함의. 본 cycle 은 *unit + mock* 만. 실제 Sysbox 통합은 `GAPT_TEST_SANDBOX=1` env 로 gating 하고 별도 `test_sysbox_real.py` 에 (생성 deferred) — CI 가 sysbox-runc 미설치 환경이므로 의도적 분리. M0-P2 의 `poc/sysbox_isolation/` 가 이미 sysbox 부팅 자체는 검증함.
- **events stream 미구현**: plan 의 `events() — docker events 스트림`. 워크스페이스 lifecycle 코드 (Cycle 1.8) + ARQ 좀비 정리가 실제로 events 를 요구할 때 추가 — 현재 API surface 가 비어있으니 deferred.
### Cycle 1.8 — Workspace 라이프사이클 (✅ 완료 — *this commit*)

- `container.py` 가 `SandboxBackend` 를 1급 의존성으로 hold. `settings.sandbox_use_real_docker=true` 일 때만 `SysboxBackend(client=make_default_client())`, 기본은 `MockSandboxBackend` (단일 노드 dev / CI 친화). `get_sandbox_backend` Depends 추가.
- `gapt_server/domains/workspaces/service.py`:
  - `WorkspaceService` — `create/list_for_project/get/start/stop/delete`
  - `create` 가 DB row 를 CREATING 으로 먼저 commit → `SandboxBackend.create+start` → 성공 시 RUNNING + `Sandbox` row 동시 commit. 실패 시 row FAILED 로 마킹 + audit `outcome=error` + `WorkspaceError("workspace.sandbox_boot_failed")` 던짐 (사용자가 진단/재시도 가능).
  - `stop/start/delete` 가 sandbox 동작 + audit emit + role 가드 (`EDITOR` for stop/start, `ADMIN` for delete) + `last_activity_at` 갱신
  - `delete` 는 sandbox 가 이미 없으면 swallow (테어다운 시 race 허용)
- `gapt_server/routers/workspaces.py` — 2 라우터 (`by_project` for create/list, `by_id` for get/start/stop/delete). `WorkspaceError` → HTTP status 매핑 (404 not_found, 409 sandbox_*).
- `projects.service.fetch_project_for` underscore 제거 (cross-domain 공용 헬퍼로 승격). `projects.http_from_project_error` 도 마찬가지.
- 테스트 4개:
  - 전체 lifecycle (create → list → get → stop → start → delete) + audit 3 이벤트 검증
  - 비-멤버 가 워크스페이스 생성 시도 → 403 `project.forbidden`
  - sandbox boot 실패 → row 가 FAILED 로 commit + 409 + audit `outcome=error` 기록
  - 존재하지 않는 workspace_id → 404 `workspace.not_found`
- 결과: 88 PASS (이전 84 → +4), ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **SSE 진행 스트림 deferred**: plan §1.8 "진행 SSE". sandbox boot 가 동기 + (Mock 백엔드 환경) 매우 빠른 경우, SSE 가치 낮음. SysboxBackend 가 prod 환경에서 부팅 시간 측정 후 (Cycle 1.9 데몬 healthcheck 통합 시점) 가산. 현재는 201 / 409 에 모든 상태가 담김.
- **ARQ 좀비 정리 task deferred**: plan §1.8 "ARQ 작업: 좀비 정리 (1시간마다)". Redis/ARQ 의존이 도입되지 않았으므로 미구현. M1-E2 가 agent_sessions 쪽 freshness ARQ 와 함께 통합 — Mock 환경에서는 좀비 자체가 생기지 않음.
### Cycle 1.9 — toolkit-agent 데몬 v1 — 2 PR

#### PR 1 (1.9a) — JWT 미들웨어 + /exec + /readfile + /writefile (✅ 완료 — *this commit*)

- `runtime/src/gapt_runtime/auth.py` — `jwt_middleware` aiohttp 미들웨어:
  - HS256 검증, `aud=gapt-runtime` / `iss=gapt-server` / `exp` / `iat` require
  - `sub` 가 `settings.session_id` 와 일치해야 함 (session pinning)
  - `/health` 만 exempt (호스트 healthcheck 가 토큰 없이 ping)
  - `GAPT_DAEMON_TOKEN` 미설정 → 500 (silent accept 금지)
- `runtime/src/gapt_runtime/handlers.py` — 3 endpoint:
  - `POST /exec` — argv 실행, base64 stdout/stderr/exit_code/duration 반환, `timeout_s` wall-clock guard (1~600s)
  - `POST /readfile` — `workspace_root` 하위 강제 (symlink resolve 포함), `max_bytes` cap (default 1MiB, max 64MiB)
  - `POST /writefile` — 같은 path guard, `create_parents` flag, `mode` chmod
  - `_resolve_under_root` 가 absolute / relative / `..` traversal / symlink-escape 모두 거부
- `runtime/src/gapt_runtime/daemon.py` 가 미들웨어 + 3 라우터 wire-up
- 테스트 12개 추가 (`test_daemon_handlers.py`):
  - JWT — /health exempt, /info 401 without token, expired/wrong-secret/session-mismatch 모두 401
  - /exec — echo round trip, sleep timeout → 408, cwd outside workspace → 403
  - /readfile + /writefile — round trip, `..` traversal 403, `/etc/passwd` 403, missing → 404, 큰 파일 + 작은 `max_bytes` → 413
  - GAPT_DAEMON_TOKEN 빈 값 → /info 500, /health 200
- 기존 smoke tests (`test_daemon_smoke.py`) 가 JWT 통과하도록 갱신 (secret 32+ chars + Bearer header)
- 결과: 22 PASS (runtime), ruff + mypy strict 그린.

#### PR 2 (1.9b) — PTY + WebSocket (✅ 완료 — *this commit*)

- `runtime/src/gapt_runtime/pty_manager.py`:
  - `PtyManager` — `pty.openpty` + `os.fork` 로 shell 스폰, master_fd non-blocking + `loop.add_reader` 로 async read, async write/resize/close, `aclose()` 가 shutdown 시 모든 세션 SIGHUP + reap zombie
  - `PtySession` 데이터클래스 (id, master_fd, pid, shell, cwd, rows, cols, closed)
  - `_set_winsize` ioctl wrapper, `_write_all` partial write retry
- `runtime/src/gapt_runtime/handlers_pty.py`:
  - `POST /open_pty` — argv shell + cwd (default `workspace_root`) + rows/cols/env, ID + PID 반환
  - `WS /pty/{id}` — xterm.js 컨벤션: binary = raw PTY bytes 양방향, text = JSON `{"type":"resize","rows":int,"cols":int}` 메시지 처리. `_pump_pty_to_ws` background task 가 PTY → WS 한 방향 펌프, finally 블록이 task cleanup + WS close.
  - `POST /pty/{id}/close` — idempotent kill+close
- `daemon.create_app()` 이 `PtyManager` 를 app state 에 hold, shutdown hook 으로 `aclose()` 호출, 3 route 추가
- 테스트 5개 (`test_daemon_pty.py`):
  - `/open_pty` → ID/PID/사이즈 반환
  - WS 연결 후 `echo gapt-ok\n` 전송 → master 출력에 sentinel 포함
  - resize JSON 메시지 → PtySession.rows/cols 갱신
  - 존재하지 않는 PTY ID → WS 404
  - close 두번 호출 → 둘 다 200 (idempotent)
- 결과: 27 PASS (runtime, 이전 22 → +5), ruff + mypy strict 그린.

##### Plan 카드 대비 변경

- **JWT 토큰 회전 (사용자 세션 수명마다)**: plan §1.9 명시. 본 cycle 은 토큰 발급/검증만, 자동 회전 로직 미구현 — server 측 컨트롤 플레인이 새 토큰 발급 후 컨테이너 env 갱신하는 흐름은 M1-E2 의 agent session lifecycle 와 연계 (현재는 컨테이너 boot 시 단일 토큰).
- **`AgentDaemonClient`**: plan §1.9 "컨트롤 측 클라이언트". server 가 daemon 을 호출할 때 쓰는 httpx wrapper — 본 cycle 미구현, server side 가 실제로 daemon 호출하는 첫 cycle 인 M1-E2 에 합류.
### Cycle 1.10 — PolicyEngine 골격 (✅ 완료 — *this commit*)

- `gapt_server/policy/engine.py`:
  - `PolicyDecision` enum (ALLOW / DENY / REQUIRE_USER_APPROVAL / REQUIRE_2FA)
  - `Actor` (kind: USER / AGENT_SESSION / SYSTEM + id), `Scope` (project/workspace/environment), `PolicyEvaluation` 결과 데이터클래스
  - `PolicyEngine.evaluate(action, actor, scope, context)` — built-in bundle 매핑 (§9.2.3 표 그대로 + `git.push.protected/force`, `tool.bash.danger`, `edit.sensitive_file` 추가). agent_session 은 deploy.* / secret.mutate / membership / git.push.force 전부 DENY (user-only action class). 알 수 없는 action 은 ALLOW (M1-E4 override 가 좁힘).
  - `audit_sink` Depends — 모든 evaluation 이 `policy.evaluate` audit row 발행, outcome 은 decision 에서 매핑 (DENY=denied, others=ok)
- `gapt_server/policy/invariants.py` — §9.2.4 5 불변식 모두 코드 강제:
  1. `require_safe_sandbox_mount` (validate_mounts 위 wrapper, 동일 거부 패턴 + InvariantViolation 으로 변환)
  2. `require_owner_id(row_kind, owner_id)` — `None` 또는 빈 문자열 거부, talkative 에러
  3. `require_secret_not_in_payload(payload)` — dict 의 `value` / `secret` / `api_key` 키 + `sk-ant-` / `sk-` / `ghp_` / `github_pat_` / `xox[baprs]-` 토큰 패턴 거부, 재귀 walking
  4. `require_audit_event_actor(declared, observed)` — actor_id 위조 차단 (system events `None==None` 허용)
  5. `require_not_agent_session(actor_kind, action)` — agent_session 이 `policy.*` 액션 invoke 시 거부
- 테스트 26개:
  - `test_engine.py` (8): deploy.prod 양쪽 actor 모두 DENY, deploy.dev 는 REQUIRE_USER_APPROVAL, agent 가 secret.mutate 시도 시 DENY (reason 에 "user-only"), secret.read 양쪽 ALLOW, force_push 모두 DENY, 알 수 없는 action ALLOW, audit sink 가 evaluation row 기록, scope+context 가 결과에 carry
  - `test_invariants.py` (18 parametrized + 단독): docker.sock 거부 + safe path 허용, owner_id None/"" 거부 + 정상 값 허용, 4가지 secret-shape (key value / nested api_key / Bearer string / list of secrets) 거부 + 4가지 safe shape 통과, audit actor mismatch 거부 + match 허용, agent → policy.* 거부 + user → policy.* 허용, agent → secret.read 통과
- 결과: 114 PASS (이전 88 → +26), ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **단일 layer**: plan §1.10 명시 ("이 cycle에선 override 시스템 X"). 4-layer override (built-in / server / org / project) 는 M1-E4. 본 cycle 의 `PolicyEngine` 인터페이스는 그대로 유지되고, 구현체만 layered 로 swap 가능.
- **2FA flow 미구현**: `REQUIRE_2FA` decision 은 정의됐지만 트리거 path 미구현. plan 의 `deploy.prod` 가 "DENY (user click + 2FA)" 인데, 실제 2FA 플로우는 M1-E3 (Web IDE) 가 가져옴 — 현재는 DENY 가 충분히 보수적.
### Cycle 1.11 — SeaweedFS 볼륨 라이프사이클 (✅ 완료 — *this commit*)

- `gapt_server/domains/storage/`:
  - `volume.py` — `VolumeManager` Protocol + `VolumeRef` 데이터클래스 (`workspace_id` / `bucket` / `path` / `filer_url` + `to_env()` 가 runtime 의 entrypoint script 가 읽을 `GAPT_SEAWEED_*` env 4개 반환)
  - `InMemoryVolumeManager` (테스트/dev), `FilerVolumeManager` (SeaweedFS filer HTTP API — `POST ?op=mkdir` / `DELETE ?recursive=true` / `GET`)
  - 인보케이션 invariants: workspace_id 가 26-char Crockford ULID 가 아니면 `volume.invalid_workspace_id`, FilerVolumeManager 가 빈 filer_url 거부 → `volume.filer_url_missing`
  - `httpx.AsyncClient` factory injection (테스트가 `httpx.MockTransport` 주입)
- 테스트 15개 (`tests/storage/test_volume.py`):
  - 7 parametrized invalid workspace_id (빈 문자열 / 짧음 / 길음 / path traversal / mixed-case / Crockford 외 문자 `I`)
  - in-memory create+delete + env 4종 검증
  - duplicate create 거부 (`volume.already_exists`)
  - Filer create POST + `op=mkdir` query 확인
  - Filer 500 응답 → `volume.filer_failed`
  - Filer DELETE recursive=true 확인
  - Filer DELETE 404 idempotent (재시도 안전)
  - Filer GET exists 200/404 분기
  - 생성자 empty url 거부
- 결과: 129 PASS (이전 114 → +15), ruff + mypy strict 그린.

#### Plan 카드 대비 변경

- **docker plugin 대신 FUSE-via-entrypoint**: M0-P2 `decision_volume_driver.md` 의 Option B 채택 — runtime image 의 `mount_seaweedfs_workspace` script 가 sandbox boot 시 FUSE mount 를 수행하고, 본 cycle 의 `VolumeManager` 는 **filer 쪽 path 만 관리**. docker plugin 은 운영 복잡도 + Sysbox 와의 호환성 이슈로 보류.
- **per-workspace bucket 대신 per-workspace path**: plan 은 "Filer collection 단위" 모호하게 명시. 구현은 단일 bucket (`settings.seaweed_bucket`, 기본 `gapt`) 아래 `/<workspace_id>` 디렉토리. 여러 GAPT 인스턴스가 SeaweedFS 클러스터 공유 시에는 bucket 만 분리.
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
