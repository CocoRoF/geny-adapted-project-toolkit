# Contributing to GAPT

GAPT는 *cadence 기반* 개발한다. 코드 한 줄도 *plan 카드 + progress 기록* 없이 머지되지 않는다. 이 문서는 그 규칙을 정리한다.

자세한 메타: [`docs/plan/00_master_plan.md`](docs/plan/00_master_plan.md), [`docs/11_roadmap.md`](docs/11_roadmap.md).

---

## 1. 모든 작업은 cycle 단위

- **cycle** = PR 1~3개에 대응하는 작업 단위.
- 각 cycle은 한 *plan 카드*(미리 작성) + 한 *progress 카드*(작업 중 갱신)를 가진다.
- cycle은 `M{n}-{P|E}{n}` 형식의 ID (예: `M0-P1`, `M1-E2`).

진행 중인 cycle 목록 + 의존 그래프: [`docs/plan/dependencies.md`](docs/plan/dependencies.md).

---

## 2. cycle 진행 흐름

```
[plan 카드 작성]
   ↓
[사용자 검토 게이트 1 — 통과 시 진행]
   ↓
[cycle 시작 — progress 카드 init, Status: in_progress]
   ↓
[작업 항목들 진행 — 매 PR 머지 시 progress에 한 줄 이상 추가]
   ↓
[DoD 모두 체크 → 검증 시나리오 통과]
   ↓
[cycle 종료 — progress에 drift 절 추가, Status: done]
   ↓
[사용자 검토 게이트 2 — 다음 cycle 진입]
```

---

## 3. PR 규칙

### PR 본문

PR 본문에 *반드시* 두 줄 명시:

```
Plan: docs/plan/{path/to/cycle.md}
Progress: docs/progress/{path/to/cycle.md}
```

GitHub PR 템플릿([`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md))이 이 필드를 강제한다.

### PR 크기

- 한 PR = 가급적 1 기능 또는 1 파일 묶음.
- 큰 cycle은 여러 PR로 쪼개 머지.

### 커밋 시그너처

LLM이 작성/공동작성한 커밋에는 다음 trailer 자동 추가 (참조: `reference_git_identity`):

```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### 머지 전 체크

- [ ] CI 그린 (lint + type + test)
- [ ] plan/progress 카드 참조 명시
- [ ] 새 docs가 필요한 변경이면 docs 업데이트 포함
- [ ] 시크릿 평문이 추가되지 않음 (`.env`, `*.key` 등 .gitignore 확인)
- [ ] 격리 시나리오에 영향 있는 변경이면 PoC 재실행

---

## 4. 코드 스타일

### Python (`server/`, `runtime/`)

- Python 3.12+
- `uv` 가상환경 + 의존성
- `ruff` (lint + format), `mypy --strict`, `pytest`
- 비동기 우선 (`asyncio`)
- 구조화 로깅 (`structlog`)

### TypeScript / React (`web/`)

- TypeScript strict
- `pnpm` 워크스페이스
- `eslint` + `prettier`
- React 19 + Vite 6
- Tailwind + shadcn/ui
- `vitest`

### 공통

- 한 줄 코멘트만 (다중 라인 docstring 금지 — 시스템 가이드)
- 함수명·변수명이 의도를 드러내야 — *코멘트로 설명을 보충*하지 말 것
- 5개 어댑터 인터페이스(Git/Sandbox/Secret/Auth IDP/PolicyEngine) 외 직접 구현 의존 금지

---

## 5. 어떻게 새 cycle을 추가하는가

새 작업 주제가 발견되면:

1. **분석이 필요한 경우** → `docs/analysis/{date}_{topic}.md` 작성 (기존 12편 docs와 분리).
2. **plan 카드** → `docs/plan/m{n}/{cycle_id}.md`. [`docs/plan/00_master_plan.md`](docs/plan/00_master_plan.md) §0.3 템플릿 사용.
3. **master plan 인덱스 갱신** → §0.6에 새 cycle 추가.
4. **dependencies.md 갱신** → 어떤 cycle이 blocking인지.
5. **사용자 검토 → 통과 시 cycle 진입**.

*절대 plan 없이 코드를 추가하지 않는다.*

---

## 6. 우리가 *하지 않는* 것

- **fork — geny-executor 등 의존 라이브러리는 fork 금지**. 능력 확장은 *해당 라이브러리에 PR*. (참조: `feedback_extend_executor_not_adapter_layer`)
- **상위 앱에 LLM 어댑터 다층화** — 모든 에이전트 호출은 geny-executor 통과.
- **시크릿 평문을 DB/로그/git에 commit** — `SecretBackend` 어댑터만 사용.
- **호스트 docker 소켓 마운트** — 어떤 모드에서도. (참조: `docs/06_isolation_and_runtime.md` T6)
- **위험 액션을 코드에 박는 if/else 차단** — *기본 deny + config 편집 가능* `PolicyEngine` 사용. (참조: `feedback_policy_config_not_hardcode`)
- **장식 이모지/포인터 카드/크로스-스테이지 빵부스러기** — 정보 밀도 우선.

---

## 7. 보안 / 시크릿

- 모든 시크릿은 `SecretBackend` 어댑터로 저장 + 단명 주입.
- LLM 응답에서 알려진 시크릿 값 정규식 마스킹.
- `.gitignore`가 `.env*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*` 등 차단.
- `.gapt/secrets/` 경로는 git 추적 X (gitignore 적용됨).

자세히: [`docs/09_security_authz_observability.md`](docs/09_security_authz_observability.md) §9.3.

---

## 8. 사용자 검토 게이트

- *각 epic/cycle 종료* 시 사용자 명시 통과 필요.
- 다음 단계 *자동 진입 금지*.
- 게이트 통과 못 한 cycle은 *추가 PR로 보강* — 다음 cycle을 *기다리지 않고 시작*하지 않는다.

---

## 9. 질문 / 보고

- 버그 / 보안: GitHub Issues 또는 직접 이메일.
- 새 cycle 제안: PR 본문에 *master plan 추가 제안* 포함.
- 분석 토론: `docs/analysis/{date}_{topic}.md`에 RFC 작성 후 PR.

GAPT가 GAPT 자신을 GAPT로 유지보수하는 것이 *M1의 두 번째 DoD*. 본 CONTRIBUTING은 그 흐름의 일부다.
