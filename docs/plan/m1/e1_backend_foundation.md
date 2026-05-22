# M1-E1: 백엔드 토대 (FastAPI / DB / Auth / Project / Sandbox / Secret)

> Status: planned
> Estimated: 12 작업일 / 12 PR
> Depends on: M0-P1, M0-P2
> Blocks: M1-E2, M1-E3, M1-E4
> Relates to: [`../../03_system_architecture.md`](../../03_system_architecture.md), [`../../06_isolation_and_runtime.md`](../../06_isolation_and_runtime.md), [`../../09_security_authz_observability.md`](../../09_security_authz_observability.md)

## 목적 (한 줄)
컨트롤 플레인 7개 도메인 중 **D1 Project / D2 Auth / D4 Sandbox / D7 Secret + D8 Audit의 최소 기둥**을 세운다. 사용자가 magic-link로 로그인, 외부 git 레포를 Project로 등록, Workspace 생성 시 Sysbox 컨테이너가 부팅되고 secret이 단명 주입되는 것까지.

## 진입 조건
- [ ] M0-P1 통과 (모노레포 + CI)
- [ ] M0-P2 통과 (Sysbox + SeaweedFS PoC 검증)
- [ ] [`03`](../../03_system_architecture.md) §3.6 도메인 분할 + 4개 플러그인 인터페이스 일독
- [ ] PostgreSQL 마이그레이션 도구로 Alembic 결정

## DoD (Definition of Done)
- [ ] `POST /api/auth/magic-link` + `GET /api/auth/magic-link/callback` 동작 → 세션 쿠키 발급
- [ ] `POST /api/projects` (외부 git URL + 기본 compose 경로 등록) → DB row + Project 좌측 트리 표시 가능
- [ ] `POST /api/projects/{pid}/workspaces` → SeaweedFS volume + Sysbox 컨테이너 부팅 + `/workspace`에 git clone + 사용자 compose dev up
- [ ] `POST /api/secrets` + `GET /api/secrets` (메타만, 평문 X) — keyring backend 1차
- [ ] 모든 mutate 액션이 `audit_events` 테이블에 ULID 정렬로 기록
- [ ] PolicyEngine 골격 (`PolicyEngine.evaluate(action, actor, scope, context)`) — 기본 deny + config 1계층 (built-in default bundle만, override는 M1-E4) 동작
- [ ] Alembic 마이그레이션 1번(`0001_init.sql`) 정의 + downgrade 통과
- [ ] OpenAPI 스펙 자동 생성, `gapt-web`이 type generation으로 동기화
- [ ] 모든 신규 코드 mypy strict + ruff 그린, 테스트 커버리지 ≥ 70%

## 작업 항목 (세부)

### Cycle 1.1 — DB 스키마 + Alembic (1 PR)
- Alembic 셋업 + 마이그레이션 `0001_init.sql`:
  - `users (id ULID PK, email, display_name, created_at, ...)`
  - `orgs (id, slug, name, owner_id FK, created_at)` — M0~M2엔 단일 "default"
  - `org_memberships (org_id, user_id, role enum, created_at, PK 복합)`
  - `projects (id, slug, owner_id, org_id, display_name, git_remote_url, git_provider enum, git_auth_secret_ref, default_compose_paths text[], compose_profile_dev text, compose_profile_prod text, created_at, archived_at nullable)`
  - `project_memberships (project_id, user_id, role enum)`
  - `environments (id, project_id, name, deploy_target_kind enum, deploy_target_config jsonb, require_2fa bool, secret_refs text[], cost_multiplier numeric, hooks jsonb)`
  - `workspaces (id, project_id, branch, worktree_path, sandbox_id nullable, status enum, port_assignments jsonb, last_activity_at)`
  - `sandboxes (id, project_id, workspace_id, status enum, container_id, image_tag, resource_limits jsonb, last_activity_at, created_at)`
  - `agent_sessions (id, project_id, workspace_id, user_id, env_manifest_id, status enum, cost_usd numeric, input_tokens bigint, output_tokens bigint, created_at, last_active_at)`
  - `secrets (id, owner_scope enum, owner_id, key_name, backend enum, backend_ref text, created_at, rotated_at nullable)`
  - `audit_events (id ULID PK, ts timestamptz, actor_type, actor_id, scope_jsonb, action, subject_jsonb, outcome enum, duration_ms, exec_code text nullable, payload jsonb)` — 월 파티션
- 인덱스: `audit_events (ts, scope_jsonb->>'project_id')`, `agent_sessions (project_id, status)`, `workspaces (project_id, status)`
- 테스트: `pytest tests/db/test_migration.py::test_upgrade_downgrade_clean`

### Cycle 1.2 — 설정 / DI 컨테이너 (1 PR)
- `gapt_server/settings.py` — pydantic-settings, 모든 외부 의존 (DB URL, Redis URL, SeaweedFS S3 endpoint, claude binary, sysbox runtime flag) env로
- `gapt_server/container.py` — dependency injection. FastAPI `Depends`로 어댑터 인스턴스 제공
- Logging: `structlog` 구조화 JSON, 모든 요청에 trace_id

### Cycle 1.3 — D2 Auth: MagicLink IDP (1 PR)
- `AuthIdp` Protocol (03 §3.6) + `MagicLinkIdp` 구현:
  - 이메일 → 1회용 토큰 (Redis 15분 ttl) → 로그인 URL
  - SMTP 발송 (개발은 콘솔 출력 모드)
  - `POST /api/auth/magic-link {email}` + `GET /api/auth/magic-link/callback?token=`
- 세션: HTTP-only secure cookie + Redis 세션 store
- `Depends(get_current_user)` middleware
- 단일 사용자 모드: 첫 로그인 사용자 자동 owner
- 테스트: `pytest tests/auth/test_magic_link_flow.py`

### Cycle 1.4 — D7 Secret Vault (1 PR)
- `SecretBackend` Protocol + `OsKeyringBackend` 구현 (또는 호스트 OS keyring 없는 경우 위해 `EncryptedSqliteBackend` fallback)
- 인터페이스:
  - `store(scope, key, value) -> SecretRef`
  - `read(ref, audit_ctx) -> str` (감사 강제)
  - `delete(ref)`
  - `rotate(ref, new) -> SecretRef`
- 평문은 *반드시* DB 저장 X. DB의 `secrets.backend_ref`는 keyring 식별자.
- API: `POST /api/secrets {scope, key, value}` + `GET /api/secrets` (목록만, 평문 X) + `DELETE /api/secrets/{id}` + `POST /api/secrets/{id}/rotate`
- 모든 read는 audit 이벤트 `secret.read` 발행
- 테스트: 평문이 DB나 로그에 새지 않는지 fuzz 테스트

### Cycle 1.5 — D8 Audit Sink (1 PR)
- `AuditSink` 인터페이스 + `PostgresAuditSink` 구현:
  - `log(action, actor, scope, subject, outcome, duration_ms=None, exec_code=None, payload=None)`
  - 동기 호출이지만 async — Redis Streams로 비동기 flush + Postgres 월 파티션 테이블에 batch insert
- ULID 생성기 (`python-ulid` 또는 자체)
- 인덱스 + 쿼리 헬퍼: `query(scope=..., action=..., from=..., to=..., limit=...)`
- 로그 마스킹: 알려진 secret 평문 정규식으로 페이로드 마스킹
- 테스트: 1000 이벤트 동시 발행 후 모두 보존 + 정렬 정확

### Cycle 1.6 — D1 Project Service (CRUD) (1 PR)
- 모델: `Project`, `Environment`, `Workspace` (read-only dataclass + pydantic schema)
- API:
  - `POST /api/projects {git_url, display_name, default_branch}`
  - `GET /api/projects`
  - `GET /api/projects/{pid}`
  - `PATCH /api/projects/{pid}`
  - `DELETE /api/projects/{pid}` (archive)
  - `POST /api/projects/{pid}/environments`
  - `GET /api/projects/{pid}/environments`
- *외부 git URL 검증*은 이 cycle에선 형식만 (실제 clone은 M1-E2 git service)
- Audit: `project.create/update/archive`
- 테스트: 권한 (다른 사용자의 프로젝트 X), 입력 검증

### Cycle 1.7 — D4 Sandbox Controller (어댑터 1차) (2 PR)
- `SandboxBackend` Protocol + `SysboxBackend` 구현:
  - `create(project_id, workspace_id, image, resources, secrets) -> SandboxRef`
  - `exec(ref, cmd) -> ExecResult`
  - `open_pty(ref, shell) -> PtyRef` (반환만 — 실 PTY 핸들링은 M1-E3)
  - `start/stop/inspect/destroy`
  - `events()` — docker events 스트림
- 호스트 docker SDK (`docker` Python) 사용
- 컨테이너 생성 시:
  - `--runtime=sysbox-runc`
  - SeaweedFS volume `gapt-seaweed-{id}` 마운트 to `/workspace`
  - host docker socket *비-마운트* 검증 (코드 레벨 강제, 마운트 옵션 화이트리스트)
  - 환경변수: `GAPT_DAEMON_TOKEN` (단명 JWT) + `GAPT_PROJECT_ID` + `GAPT_WORKSPACE_ID`
  - 리소스 제한 cgroup v2
- 데몬 헬스체크 (`/run/agent.sock` 접속 대기 최대 60초)
- 정리: 30분 idle → `paused` 상태 (TickEngine via ARQ)

### Cycle 1.8 — Workspace 라이프사이클 (1 PR)
- `POST /api/projects/{pid}/workspaces {branch, name?}` → SandboxBackend.create → DB row → 진행 SSE
- `GET /api/workspaces/{wid}/status`
- `POST /api/workspaces/{wid}/stop` / `start`
- `DELETE /api/workspaces/{wid}` (sandbox 정리 + SeaweedFS volume 보존 옵션)
- ARQ 작업: 좀비 정리 (1시간마다)

### Cycle 1.9 — toolkit-agent 데몬 v1 (runtime 측, 2 PR)
- `runtime/src/gapt_runtime/daemon.py`:
  - Unix socket HTTP 서버 (aiohttp 또는 starlette)
  - `POST /exec` — 임의 명령 실행 + stdout/stderr/exit code 반환
  - `POST /readfile`, `POST /writefile` — 워크스페이스 안 한정 (path traversal 거부)
  - `POST /open_pty`, `WS /pty/{id}` — xterm.js 친화 PTY
  - JWT 검증 (`GAPT_DAEMON_TOKEN`)
- 인증 토큰 회전: 사용자 세션 수명마다 회전
- 컨트롤 측 클라이언트 `AgentDaemonClient` (server에서 사용)

### Cycle 1.10 — PolicyEngine 골격 (1 PR)
- `gapt_server/policy/engine.py`:
  - `PolicyDecision` enum: `ALLOW | DENY | REQUIRE_USER_APPROVAL | REQUIRE_2FA`
  - `PolicyEngine.evaluate(action, actor, scope, context) -> PolicyDecision`
  - 기본 정책 bundle (코드 상수, [09](../../09_security_authz_observability.md) §9.2.3 표 그대로):
    - `deploy.prod` → DENY
    - `deploy.dev|staging` → REQUIRE_USER_APPROVAL
    - `secret.create|update|delete` → DENY (UI/user 호출만)
    - `secret.read` → ALLOW
    - 그 외 → ALLOW
  - **이 cycle에선 override 시스템 X**, 기본 bundle만. M1-E4에서 project/env override.
- 코드 강제 불변식 5개 (§9.2.4): 별도 모듈 `policy/invariants.py` — *모든 PolicyEngine 외부에서도* 검증되도록 가드 헬퍼.

### Cycle 1.11 — SeaweedFS 볼륨 라이프사이클 (1 PR)
- `SeaweedVolumeManager`:
  - `create(workspace_id) -> volume_name` (SeaweedFS Filer collection 단위)
  - `mount(volume_name, sandbox_ref, target="/workspace")` — Sandbox 생성 시 호출
  - `delete(volume_name)` — 워크스페이스 삭제 시 호출
- M0-P2의 결과(decision_volume_driver.md)에 따라 docker plugin or FUSE mount 채택
- 테스트: 볼륨 생성/마운트/삭제 + 컨테이너 재기동 후 데이터 유지

### Cycle 1.12 — 통합 + 검증 (1 PR)
- End-to-end pytest:
  - magic-link 로그인 → 프로젝트 생성 → 워크스페이스 생성 → sandbox 부팅 확인 → workspace 안에 git clone 실행 → workspace 삭제 → sandbox 정리 확인
- 모든 audit 이벤트가 기록되는지 fixture로 assert
- OpenAPI export → `web/src/api/types.ts` 자동 생성 (ts-codegen)
- 메모리 누수 / 자원 누수 smoke (10회 반복)

## 산출물
```
server/src/gapt_server/
├── app.py                              # FastAPI 라우터 합본
├── settings.py
├── container.py                        # DI
├── routers/
│   ├── auth.py
│   ├── projects.py
│   ├── workspaces.py
│   ├── secrets.py
│   └── audit.py
├── domains/
│   ├── auth/{idp.py, magic_link.py, session.py}
│   ├── projects/{models.py, service.py}
│   ├── workspaces/{models.py, service.py, lifecycle.py}
│   ├── secrets/{vault.py, keyring_backend.py}
│   ├── sandbox/{backend.py, sysbox_backend.py, daemon_client.py}
│   └── audit/{sink.py, postgres_sink.py, ulid.py}
├── policy/
│   ├── engine.py
│   ├── default_bundle.py
│   └── invariants.py
├── db/
│   ├── base.py
│   ├── models.py
│   └── migrations/0001_init.py
└── utils/{seaweed.py, masking.py}

runtime/src/gapt_runtime/
└── daemon.py                           # PTY + exec + read/write + JWT

tests/ (server)
├── auth/
├── projects/
├── workspaces/
├── sandbox/
├── secrets/
├── policy/
└── e2e/test_e1_smoke.py
```

## 검증 시나리오
1. magic-link 로그인 → 세션 쿠키 발급 → `/api/me` 호출 통과.
2. 외부 git URL로 프로젝트 등록 → DB row 확인 + 좌측 트리 API 응답 포함.
3. 워크스페이스 생성 → Sysbox 컨테이너 `running` 상태 + `/workspace`에 SeaweedFS 마운트 + 호스트 docker.sock 미마운트 검증.
4. Secret 등록 → DB에 평문 0 (`SELECT * FROM secrets WHERE backend_ref LIKE '%real-key%'` 결과 없음).
5. PolicyEngine.evaluate(`deploy.prod`, ...) → `DENY` 반환.
6. PolicyEngine.evaluate(`secret.read`, ...) → `ALLOW`.
7. 1000 audit 이벤트 동시 발행 후 모두 보존 + ts 순 정렬.
8. workspace 삭제 → sandbox 정리 + SeaweedFS volume 옵션에 따라 유지/삭제.

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|
| Sysbox runtime이 일부 docker 옵션과 충돌 (예: `--security-opt seccomp`) | 중 | M0-P2에서 검증된 옵션 조합만 사용, 새 옵션 추가 시 격리 시나리오 재실행 |
| keyring backend가 헤드리스 서버에서 동작 안 함 | 중 | `EncryptedSqliteBackend` fallback (passphrase from env) 동시 구현 |
| Audit이 *모든* mutate 액션을 잡지 못해 누락 | 큼 | 데코레이터 `@audited(action="...")` 강제, lint rule로 누락 감지 |
| Alembic 마이그레이션이 jsonb 컬럼에서 까다로움 | 작음 | 마이그레이션을 작은 단위로 분리, autogenerate 보조 |
| PostgreSQL의 jsonb 검색이 audit 쿼리에서 느림 | 중 | `scope_jsonb->>'project_id'` 표현식 인덱스 미리 |
| `sysbox-runc` 부팅 시간이 5초 초과 (UX 목표 위반) | 중 | 이미지 layer 캐시 최적화 (멀티 스테이지), 미리 부팅된 *템플릿 컨테이너 풀* 검토 (M2 이후) |

## 관련 docs
- [`../../03_system_architecture.md`](../../03_system_architecture.md) §3.2 도메인, §3.3 데이터 모델, §3.6 어댑터 인터페이스
- [`../../06_isolation_and_runtime.md`](../../06_isolation_and_runtime.md) §6.3 컨테이너 구조, §6.7 데몬 책임
- [`../../09_security_authz_observability.md`](../../09_security_authz_observability.md) §9.1 인증, §9.2 RBAC, §9.3 시크릿, §9.4 감사
- [`../../05_git_workflow.md`](../../05_git_workflow.md) §5.2 단명 askpass (M1-E2에서 활용)
