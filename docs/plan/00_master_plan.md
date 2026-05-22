# 00. 마스터 플랜 (Master Plan)

> **위치**: `docs/plan/`
> **상위**: [../11_roadmap.md](../11_roadmap.md)
> **자매**: `docs/progress/` (각 cycle 완료 시 동일 ID로 진행 기록)
> **작성일**: 2026-05-22

이 문서는 GAPT의 **모든 cycle을 한눈에 보는 인덱스**다. 각 cycle은 *PR 1~3개*에 대응하는 작업 단위로, 자기 폴더(또는 카드)에 진입조건·DoD·작업항목·산출물·리스크를 명시한다.

---

## 0.1 cadence 규칙

[[project_geny_plan_layout]] / [[feedback_durable_instructions]] 기반:

1. **모든 작업은 cycle 단위**. cycle은 *PR 1개 이상*의 단위로 묶이며, 한 cycle = 한 plan 카드 + 한 progress 기록.
2. **cycle 시작 전 plan 카드 작성**. 진입조건/DoD/작업항목/산출물/검증을 *먼저* 적는다.
3. **cycle 진행 중 progress 카드 갱신**. 매 PR 시점에 한 줄 이상.
4. **cycle 완료 시 progress 카드 종료**. *현실이 plan과 어떻게 달랐는지* 한 절 추가 (drift 기록).
5. **사용자 검토 게이트**: 각 epic/cycle 종료 시 사용자 명시 통과 필요. 다음 단계 자동 진입 금지.
6. **분석이 필요해진 새 주제는 `docs/analysis/<date>_<topic>.md`** — 본 12편 분석 docs와 분리되며 cycle 도중 자유 생성.
7. **manifest는 단일 진실원** ([[reference_geny_executor_v2_1]]). 환경 설정 변경은 항상 manifest를 거친다.

---

## 0.2 단계 ↔ cycle 매트릭스

| 단계 | cycle | 디테일 깊이 | 위치 |
|---|---|---|---|
| **M0** docs-first / PoC | M0-P1, M0-P2, M0-P3 | **상세** | [`m0/`](m0/) |
| **M1** 첫 통합 워크플로 | M1-E1, M1-E2, M1-E3, M1-E4 | **상세** | [`m1/`](m1/) |
| M2 멀티 프로젝트 + UX | (M2 윤곽만) | 윤곽 | [`m2_m5_outline.md`](m2_m5_outline.md) |
| M3 멀티 사용자 + OIDC | (M3 윤곽만) | 윤곽 | [`m2_m5_outline.md`](m2_m5_outline.md) |
| M4 K8s / 엔터프라이즈 | (M4 윤곽만) | 윤곽 | [`m2_m5_outline.md`](m2_m5_outline.md) |
| M5 자동 운영 / 비즈니스 | (M5 윤곽만) | 윤곽 | [`m2_m5_outline.md`](m2_m5_outline.md) |

> *주의*: M2~M5는 *지금 디테일하게 짜지 않는다*. M1 종료 시점에 학습된 현실을 반영해 M2를 디테일화하고, 그 시점에 M3 윤곽을 다시 손본다. 예측 정확도가 낮은 단계를 같은 깊이로 짜는 건 낭비.

cycle 간 의존성은 [`dependencies.md`](dependencies.md).

---

## 0.3 cycle 카드 템플릿

모든 cycle 카드는 다음 형식. 빈 항목은 *"해당 없음"*으로 명시.

```markdown
# {ID}: {제목}

> Status: planned | in_progress | done
> Estimated: {n} 작업일 / {n} PR
> Depends on: {이전 cycle ID들 또는 외부 조건}
> Blocks: {다음 cycle ID들}
> Relates to: {본 cycle을 정당화하는 docs/ 절들}

## 목적 (한 줄)
{cycle이 완료되면 무엇이 가능해지는가}

## 진입 조건
- [ ] {이 cycle을 시작하기 위해 *이미* 갖춰져 있어야 할 것들}

## DoD (Definition of Done)
- [ ] {완료를 측정 가능한 *결과*로 기술}

## 작업 항목 (세부)
### 1. {소항목}
- ...

## 산출물
- `path/to/file.{py,ts,md,yaml}` — {역할 한 줄}

## 검증 시나리오
1. ... → 기대 결과 ...

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|

## 관련 docs
- [`../{nn}_*.md`](../{nn}_*.md) §{n.n}
```

---

## 0.4 PR 정책 (반복 cadence)

[[feedback_durable_instructions]]: continuous PR cadence + always reference existing plan/progress.

- 각 cycle은 **여러 작은 PR로 쪼개서** 머지 (한 PR = 가급적 1 파일 묶음 또는 1 기능).
- PR 본문에 `Plan: docs/plan/{path}` + `Progress: docs/progress/{path}` 항상 명시.
- 머지 후 즉시 progress 카드 갱신 (지연 X).
- 커밋 시그너처 [[reference_git_identity]] 따름.
- prod 배포가 포함되는 cycle은 [09](../09_security_authz_observability.md) §9.2.3 PolicyEngine 게이트 검증 결과를 progress에 기록.

---

## 0.5 cycle 진행 흐름

```
[plan 카드 작성]                  ← 사용자 검토 게이트 1
   │
   ▼
[cycle 시작 — progress 카드 init] ← Status: in_progress
   │
   ▼
[작업 항목들 진행 — PR마다 progress 한 줄 추가]
   │
   ▼
[DoD 모두 체크 → 검증 시나리오 통과]
   │
   ▼
[cycle 종료 — progress 카드 마무리: drift 절 추가, Status: done]
   │
   ▼
[사용자 검토 게이트 2 — 다음 cycle 진입]
```

검토 게이트 1을 통과하지 못한 plan은 *되돌려서 보강*, 게이트 2를 통과하지 못한 cycle은 *추가 PR로 보강*. 다음 cycle을 *기다리지 않고 시작*하지 않는다.

---

## 0.6 인덱스 (현재 등록된 cycle)

### M0 — docs-first / PoC

| ID | 제목 | Status | Estimated |
|---|---|---|---|
| **M0-P1** | [모노레포 셋업 + CI](m0/p1_monorepo_ci.md) | planned | 3d / 4 PR |
| **M0-P2** | [격리 + SeaweedFS PoC](m0/p2_isolation_seaweedfs.md) | planned | 5d / 6 PR |
| **M0-P3** | [에이전트 + MCP bridge PoC](m0/p3_agent_mcp_bridge.md) | planned | 5d / 6 PR |

### M1 — 첫 통합 워크플로 (Tracer Bullet)

| ID | 제목 | Status | Estimated |
|---|---|---|---|
| **M1-E1** | [백엔드 토대 (FastAPI/DB/Auth/Project/Sandbox/Secret)](m1/e1_backend_foundation.md) | planned | 12d / 12 PR |
| **M1-E2** | [에이전트 세션 + Git 통합](m1/e2_agent_and_git.md) | planned | 10d / 10 PR |
| **M1-E3** | [Web IDE 셸 (Monaco/dockview/xterm/chat SSE/diff)](m1/e3_web_ide_shell.md) | planned | 12d / 14 PR |
| **M1-E4** | [통합 (Deploy/Audit/Policy/Dogfood/Geny 어댑트)](m1/e4_integration_dogfood_geny.md) | planned | 10d / 12 PR |

### M2~M5 — 윤곽

[`m2_m5_outline.md`](m2_m5_outline.md) 참조.

---

## 0.7 외부 의존성 / 사전 조건

GAPT의 작업 전반에 영향을 미치는 *외부 자원*:

| 자원 | 현재 상태 | M0~M1 단계의 의존 |
|---|---|---|
| `geny-executor` 2.1.0+ PyPI | 사용 가능 | 모든 cycle |
| `claude` CLI (Anthropic) | 사용자 PC + 컨테이너 base에 설치 필요 | M0-P3, M1-E2 |
| Sysbox runc | 호스트에 별도 설치 (apt 또는 GitHub release) | M0-P2 |
| SeaweedFS 단일 노드 | compose로 부팅 | M0-P2 |
| GitHub OAuth App | 사용자가 [github.com/settings/developers](https://github.com/settings/developers)에 등록 | M1-E2 |
| Anthropic API 토큰 (OAuth or API key) | 사용자 자체 | M0-P3 |
| 호스트 OS Linux + Docker Engine | 사용자 호스트 | M0~ |

설치/등록 가이드는 *각 cycle의 진입조건*에 구체적으로 적힘.

---

## 0.8 코드 레이아웃 (M1 종료 시점 목표)

```
geny-adapted-project-toolkit/
├── docs/                       # 12편 분석 + plan/ + progress/
├── analysis/                   # 신규 주제 심층 분석 (cycle 도중 자유 추가)
├── compose/                    # 자체 배포 compose
│   ├── docker-compose.yml      # production
│   ├── docker-compose.dev.yml  # 개발
│   └── seaweed/                # SeaweedFS Master/Filer/Volume 설정
├── server/                     # 컨트롤 플레인 (FastAPI)
│   ├── pyproject.toml
│   ├── src/gapt_server/
│   │   ├── app.py
│   │   ├── domains/            # D1~D8
│   │   ├── adapters/           # GitProvider/SandboxBackend/SecretBackend/AuthIdp
│   │   ├── agent/              # ProjectAwareSessionManager + GaptEnvironmentService
│   │   ├── manifests/          # gapt_default.json 등
│   │   ├── mcp_bridge/         # CLI MCP wrap stdio server
│   │   ├── policy/             # PolicyEngine
│   │   ├── audit/              # AuditSink + emitter
│   │   ├── migrations/         # Alembic
│   │   └── ...
│   └── tests/
├── runtime/                    # gapt/runtime 컨테이너 이미지
│   ├── Dockerfile
│   ├── pyproject.toml          # toolkit-agent
│   └── src/gapt_runtime/
│       └── daemon.py
├── web/                        # 프론트엔드 (Vite + React)
│   ├── package.json
│   ├── src/
│   │   ├── app/
│   │   ├── ide/                # dockview shell + Monaco + xterm
│   │   ├── chat/               # 채팅 패널 + SSE
│   │   ├── api/                # 타입 + fetch
│   │   └── i18n/               # ko + en, exec.*.* 코드 매핑
│   └── public/
├── caddy/                      # Caddy 설정 템플릿
├── poc/                        # M0 PoC artifacts
│   ├── sysbox_isolation/
│   ├── seaweedfs_bootstrap/
│   ├── executor_agent/
│   └── mcp_bridge/
├── scripts/                    # 사용자/관리 스크립트
└── README.md
```

각 cycle은 *이 레이아웃의 일부*를 구현. 부분이 모이면 M1 종료 시 위 구조 완성.

---

## 0.9 cycle 추적 상태 머신

```
planned ──(검토 통과)──► in_progress ──(DoD 충족 + 검증)──► done
   │                          │
   │                          └──(blocker 발견)─► blocked
   │
   └──(우선순위 변경)─► deferred
```

각 카드 상단의 `Status:` 필드를 항상 최신화. `blocked`는 *왜 blocked인지* 본문에 명시.

---

## 0.10 비-목표 (이 plan 단계에서)

- M0/M1 cycle을 *모두 한꺼번에 진행*하지 않는다 — depends_on을 따라 직렬/병렬 결정.
- M2~M5 plan을 *지금 디테일하게 짜지 않는다*.
- *cycle 진행 도중 새 cycle을 임의 추가*하지 않는다 — 새 작업이 필요하면 master plan에 등록 후 진행.
- *문서가 코드에 뒤처지지 않도록* — plan/progress 갱신 없는 PR은 자체 거부.

---

## 0.11 다음 행동

1. 사용자가 본 master plan + M0/M1 cycle 카드 검토.
2. 검토 통과 시 `M0-P1` 진입조건 확인 + Status를 `in_progress`로 변경 + `docs/progress/m0/p1_monorepo_ci.md` 신규 생성.
3. M0-P1 진행 → DoD 통과 → 사용자 검토 → M0-P2 진입.

각 카드의 디테일은 다음 페이지부터.
