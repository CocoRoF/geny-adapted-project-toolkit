# M2 Phase H — Environment editor 통합/구조화

> **상위**: [`00_master_plan.md`](00_master_plan.md) · [`m2_m5_outline.md`](m2_m5_outline.md)
>
> Status: done (2026-05-28)
> Estimated: 1.5 작업일 / 3–4 PR
> Depends on: Phase B-Hardening (B.H.3 §5 의 EnvSettingsModal 자동 re-diagnose) 완료
> Blocks: 없음 (v1 종료 게이트 외부 추가 cycle)
> Relates to: [`../09_security_authz_observability.md`](../09_security_authz_observability.md), [`m2_phase_b_hardening.md`](m2_phase_b_hardening.md) §B.H.3 §5

## 목적 (한 줄)

신규 생성 모달이 `EnvSettingsModal` 와 같은 수준의 *구조화된 폼* 이
되고, 백엔드가 `deploy_target_kind` 별 스키마로 `deploy_target_config`
를 검증한다 — raw JSON 박스 제거.

---

## 왜 지금

오늘 기준 환경 에디터가 *두 개*로 갈라져 있다:

| 에디터 | 위치 | 다루는 필드 | 결함 |
|---|---|---|---|
| `NewEnvironmentModal` (`routes/Environments.tsx`) | Project → Environments + 우상단 `+` | `name`, `kind`, `deploy_target_config` (**raw JSON textarea**), TLS-terminator 토글, `require_2fa`, `cost_multiplier` | 1급 필드(`compose_path`)도 손으로 JSON 작성. preview_mode / upstream_* 등은 노출 안 됨. 잘못된 JSON 입력 시 422 만 받고 어디가 틀렸는지 불명확 |
| `EnvSettingsModal` (`ide/EnvSettingsModal.tsx`, 1192 LOC) | Deploy view → env card → ⚙ Edit | preview_mode, preview_slug, primary_service/port, upstream_*, strip_prefix, build, 4개 프리셋, subdomain 진단 가이드 | 풀스펙 구조화 에디터인데 **신규 생성에서 도달 불가**. 기존 env 의 routing 만 손볼 수 있음. |

→ "환경 만들기" 와 "환경 편집" 이 *같은 데이터* 를 다루면서 UI 가 분리됨.
사용자 입장에서 "어디에서 어떤 필드를 손대야 하는가" 가 일관되지 않음.

백엔드는 `deploy_target_config: dict[str, Any]` — kind 별 스키마 검증
부재. 잘못된 키/타입을 그대로 받아서 deploy 직전에 KeyError / TypeError
로 터지는 잠복 버그 면을 만들고 있음.

---

## 진입 조건

- [x] B.H.3 §5 (EnvSettingsModal 자동 re-diagnose) 완료 → 통합 에디터의
      "Save & re-route" 동작 기반
- [x] `EnvironmentResponse.deploy_target_config` API 안정 (P/G/D 동안 변경 없음 확인)
- [x] 사용자 confirm: H.1→H.4 전부, TLS-terminator 는 통합 에디터 프리셋으로 흡수 (2026-05-28)

## DoD (Phase H 완료 게이트)

- [ ] 신규 환경 생성 시 raw JSON textarea 가 *어디에도* 노출되지 않음
- [ ] `local` / `remote_ssh` / `webhook` 각 kind 별 1급 필드가 폼으로 검증됨
- [ ] 백엔드 POST/PATCH `/environments` 가 kind 별 pydantic 스키마로 검증; 잘못된 필드 → 422 + `field` 별 에러 메시지
- [ ] EnvSettingsModal 의 4개 프리셋 + NewEnvironmentModal 의 TLS-terminator 토글이 *하나의* 프리셋 그룹으로 통합
- [ ] 기존 environment row 는 read-side 에서 깨지지 않음 (legacy `deploy_target_config` 잡힐 때 graceful)
- [ ] 새 unit test: kind 별 검증, 빈 config / 잘못된 키 / strict 타입 위반
- [ ] 시각 검증: 사용자 / assistant 가 신규 환경 1회 생성 + 기존 환경 편집 1회 통과

---

## 작업 항목

### H.1 — Backend per-kind pydantic discriminated config

**산출물**:
- `server/src/gapt_server/domains/environments/target_config.py` — 신규
  모듈, 다음 3개 pydantic 모델 export:
  - `LocalTargetConfig` — compose_path, compose_paths, preview_mode,
    preview_slug, primary_service, primary_port, upstream_*, strip_prefix,
    build. 모두 optional 이지만 타입 strict.
  - `RemoteSshTargetConfig` — host (req), user (default "deploy"),
    port (1..65535, default 22), key_secret_ref (vault id), compose_path.
  - `WebhookTargetConfig` — url (HttpUrl), secret_ref, env_keys (list[str]).
- `validate_target_config(kind: DeployTargetKind, raw: dict) -> dict`
  — 라우터에서 호출하는 단일 진입점. 잘못된 입력 → `pydantic.ValidationError`
  raise. 결과는 `model_dump(exclude_none=False)` 로 다시 dict 반환
  (DB 컬럼 타입 유지).
- `server/src/gapt_server/routers/projects.py` `create_environment`
  + `server/src/gapt_server/routers/environments.py` `update_environment`
  → POST/PATCH 진입부에 `validate_target_config()` 호출, ValidationError
  → 422 with `{code: "environment.target_config_invalid", reason, fields: [...]}`.
- k8s 는 명시적으로 `not_supported` 422 반환.

**검증 (단위 테스트)**:
- `server/tests/domains/environments/test_target_config.py` —
  - kind=local: 빈 dict 통과, `primary_port=99999` → ValidationError,
    `upstream_scheme="ftp"` → ValidationError, 정상 값 round-trip
  - kind=remote_ssh: host 누락 → ValidationError, port 범위 위반,
    정상 round-trip
  - kind=webhook: url 누락 → ValidationError, 잘못된 URL → ValidationError
  - kind=k8s: validate_target_config 가 `NotSupportedError` raise

**검증 (HTTP)**:
- `server/tests/projects/test_routes.py` 확장: 잘못된 compose_path
  타입 → 422 + 필드명 노출

**중요**: read-side 는 변경 X. 기존 row 의 dict 가 새 스키마와 안 맞아도
list/get 은 그대로 동작 (write-time enforcement only).

---

### H.2 — Frontend unified `EnvironmentEditor`

**산출물**:
- `web/src/environments/EnvironmentEditor.tsx` — 신규. props:
  ```ts
  {
    mode: "create" | "edit";
    projectId: string;
    initial?: EnvironmentResponse;  // edit 일 때만
    onSaved: (env: EnvironmentResponse) => void;
    onCancel: () => void;
  }
  ```
- 구성 (kind 별 조건부):
  1. **Basic** — name (편집모드에서 read-only), kind selector (편집모드 disabled),
     2FA, cost_multiplier
  2. **Local Compose** (kind=local) — compose_path, compose_paths (multi),
     "프리셋" 패널 (4 from EnvSettingsModal + TLS-terminator), routing
     섹션, upstream 섹션, deploy(build) 섹션, subdomain setup guide
  3. **Remote SSH** (kind=remote_ssh) — host/user/port/key_secret_ref/compose_path
  4. **Webhook** (kind=webhook) — url/secret_ref/env_keys (chips)
  5. **K8s** — disabled with explanation banner
- kind 변경 시 H.3 의 기본 템플릿으로 seed.
- "Save" 만 (create) / "Save" + "Save & re-route" (edit, deploy view 와
  동일한 액션 표면).
- `web/src/routes/Environments.tsx` 의 `NewEnvironmentModal` 삭제 →
  `EnvironmentEditor mode="create"` 를 같은 Modal 안에서 호스팅.
- `web/src/ide/EnvSettingsModal.tsx` 의 form/preset/upstream/deploy 부분 →
  `EnvironmentEditor mode="edit"` 로 이관. **subdomain 진단 가이드 + reroute**
  는 EnvSettingsModal 안에 남기되 EnvironmentEditor 위에 wrapper 로 합성
  (subdomain 진단은 deploy view 전용 컨텍스트라 create 모달엔 불필요).

**중요한 UX 결정**:
- "raw JSON 보기" 토글은 advanced 영역에 두지 *않음* — 사용자가 JSON
  으로 도망갈 수 있게 만들면 H.1 의 검증을 우회할 수 있음. 단,
  legacy 키가 있는 기존 env 의 편집 모드에서는 "확장 키 (스키마 외부)"
  read-only viewer 를 보여주고 무시되는 키임을 표시.
- preview_mode = "subdomain" 일 때만 preview_slug, subdomain 진단 가이드 노출.

---

### H.3 — Per-kind initial templates

**산출물** (EnvironmentEditor 안 helper):
- `defaultsFor(kind)`:
  - local: `{compose_path: "docker-compose.yml", strip_prefix: true, preview_mode: "path"}`
  - remote_ssh: `{port: 22, user: "deploy", compose_path: "docker-compose.yml"}`
  - webhook: `{env_keys: []}`
- create 모드에서 kind selector 가 바뀌면 form 을 `defaultsFor(newKind)`
  로 reset (사용자가 손댄 필드 보존 X — kind 가 바뀌면 의미 자체가 달라짐).
- edit 모드에서는 kind 변경 disabled (kind 바꾸려면 새로 만들라).

---

### H.4 — Tests + drift + memory

**Tests**:
- `server/tests/domains/environments/test_target_config.py` — H.1 의
  스키마 단위.
- `server/tests/projects/test_routes.py` 확장 — POST `/environments`
  잘못된 config → 422 with fields.
- `web/tests/environments/EnvironmentEditor.test.tsx` (vitest) —
  스모크: create 모드 kind 토글 시 form reset 확인, local→remote_ssh
  전환, 잘못된 입력 → 422 reason 표시.

**Drift**:
- `docs/progress/m2_phase_h.md` 에 cycle 종료 시 *plan 과 어떻게 달랐는지*
  한 절. (예: EnvSettingsModal 의 subdomain 진단을 합쳐버릴까 고민했으나
  분리 유지가 더 깔끔했다, 등)

**Memory 업데이트**:
- `feedback_*` 가 필요한 패턴이 발견되면 추가 (예: "deploy_target_config
  스키마는 write-time enforce only"). 발견 안 되면 update 없음.

---

## 산출물 요약

```
server/
  src/gapt_server/domains/environments/__init__.py            (신규 dir)
  src/gapt_server/domains/environments/target_config.py       (신규)
  src/gapt_server/routers/projects.py                          (수정)
  src/gapt_server/routers/environments.py                      (수정)
  tests/domains/environments/__init__.py                       (신규)
  tests/domains/environments/test_target_config.py             (신규)
  tests/projects/test_routes.py                                (확장)

web/
  src/environments/EnvironmentEditor.tsx                       (신규)
  src/routes/Environments.tsx                                  (수정 — NewEnvironmentModal 위임)
  src/ide/EnvSettingsModal.tsx                                 (수정 — 편집 위임)
  tests/environments/EnvironmentEditor.test.tsx                (신규)
  src/i18n/en.ts / ko.ts                                       (확장)

docs/
  plan/m2_phase_h.md                                           (이 파일)
  plan/00_master_plan.md                                       (index 행 추가)
  progress/m2_phase_h.md                                       (신규)
```

---

## 검증 시나리오

1. **Create local** — Environments → "+ New environment" 클릭 → name="staging",
   kind="local", compose_path 자동 채워짐, preset "Next.js dev" 클릭 →
   primary_service=frontend, primary_port=3000, strip_prefix=true 자동.
   Save → 201, 목록에 staging 카드 표시.
2. **Validation fail** — primary_port 99999 입력 → Save → 422,
   에러 카드에 "primary_port: must be ≤ 65535".
3. **Edit + reroute** — 기존 prod env ⚙ → primary_port 4000 → Save & reroute
   → reroute 200, Caddy 라우트 갱신 확인.
4. **Kind switch in create** — kind=local → webhook 전환 시 compose 필드
   사라지고 url/secret 필드 등장, 입력값 reset.
5. **Legacy row open** — 기존 row 의 deploy_target_config 가 unknown 키 포함 →
   edit 모드 진입해도 폼 정상 채움, unknown 키는 "확장 키" 영역에서 read-only 로 표시.

---

## 리스크 + 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| 기존 row 의 잘못된 config 가 read-side 에서 깨짐 | environments 페이지 렌더 실패 | read 는 검증 안 함 — H.1 의 validate 는 write 진입부에서만 호출 |
| pydantic 스키마가 너무 엄격해서 valid 한 legacy 값까지 reject | 운영 마찰 | Optional 위주 + `extra="allow"` 로 unknown 키 허용, 단 *알려진* 필드는 타입 strict |
| 통합 에디터 한 컴포넌트가 너무 비대해짐 | 유지보수 부담 | 섹션별 sub-component 로 분리 (RoutingSection, UpstreamSection, RemoteSshSection 등) |
| EnvSettingsModal 의 subdomain 진단 가이드를 합치다 deploy-view 컨텍스트 깨짐 | 진단 UX 회귀 | 진단은 EnvSettingsModal 안에 남기고 form 만 위임 (작업 항목 §H.2 의 결정) |
| webhook/remote_ssh 폼만 구현하고 실제 deploy 경로는 미구현 → 사용자 혼란 | "왜 만들었는데 deploy 가 안 돼?" | UI 에 명시적 "Phase H 범위: 폼만, deploy 실행은 별도 cycle" 배너. Out-of-scope 섹션에 명문화 |

---

## Out of scope (이번 cycle 아님)

- `remote_ssh` deploy 실제 실행 경로 (paramiko + compose-over-ssh + key
  vault 통합) — 별도 cycle 후보
- `webhook` deploy 실제 호출 + retry/backoff — 별도 cycle 후보
- `k8s` SandboxBackend / DeployTarget — 영구 v1 out-of-scope ([`m2_m5_outline.md`](m2_m5_outline.md))
- `deploy_target_config` 의 alembic 데이터 마이그레이션 (legacy row 정리) —
  solo-hobby 운영 규모상 불필요. 발생 시 사용자가 edit 모달에서 수동 정정.

---

## 관련 docs

- [`../09_security_authz_observability.md`](../09_security_authz_observability.md) §환경별 정책 — require_2fa / cost_multiplier 가 검증되어야 한다는 일반 원칙
- [`m2_phase_b_hardening.md`](m2_phase_b_hardening.md) §B.H.3 §5 — 자동 re-diagnose 의존
- [[feedback_extend_executor_not_adapter_layer]] — 새 deploy target 추가 시 어댑터 다층화 금지 원칙 (H.2 의 form 통합 결정 근거)
