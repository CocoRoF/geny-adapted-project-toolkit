# Progress — M2 Phase H (Environment editor 통합/구조화)

> Plan: [`../plan/m2_phase_h.md`](../plan/m2_phase_h.md)
> Status: in_progress (started 2026-05-28)

## Timeline

- **2026-05-28** — cycle 개시. Plan 카드 작성 ([`../plan/m2_phase_h.md`](../plan/m2_phase_h.md)),
  00_master_plan.md 인덱스 행 추가. 사용자 confirm: H.1→H.4 전부 + TLS-terminator
  는 통합 에디터 프리셋으로 흡수.
- **2026-05-28** — H.1 완료. `domains/environments/target_config.py` (Local /
  RemoteSsh / Webhook pydantic 모델 + `validate_target_config` dispatcher).
  `ProjectService.create_environment` + `environments.py` create/update PUT
  모두 진입부에서 호출, 실패 시 422 + `fields[]`. `K8S` 는 명시적
  `KindNotSupportedError`. 테스트 15 단위 + 1 HTTP integration (`tests/domains/environments/test_target_config.py` + `tests/projects/test_routes.py::test_environment_target_config_validation`). 20/20 pass.
- **2026-05-28** — H.2 + H.3 완료. `web/src/environments/EnvironmentEditor.tsx`
  신규 (controlled-component, props: mode/form/onFormChange/fieldErrors/disabled
  + `extraBelowKindSection` 슬롯). `readForm` / `writeForm` / `defaultsFor`
  helper export. 4 시나리오 프리셋 + TLS-terminator 통합 (Phase H 결정대로
  단일 프리셋 row). `routes/Environments.tsx` 의 NewEnvironmentModal +
  `ide/EnvSettingsModal.tsx` 모두 EnvironmentEditor 위임으로 정리 (EnvSettingsModal
  1192 → 588 LOC). EnvSettingsModal 은 SubdomainSetupGuide + reroute + help
  modal 만 wrapper 책임. 422 `fields[]` → 폼 필드 inline 표시. tsc clean,
  lint 새 파일 0 error (3 react-refresh warnings, harmless).
- **2026-05-28** — H.4 live smoke. 3 시나리오 (bad port → 422 + fields /
  k8s → 422 target_kind_not_supported / 정상 local with extras → 201,
  extras 보존) 모두 통과. 서버 backend 20/20 pass.
- (다음 항목은 PR 머지 시점에 한 줄씩 append)

## Drift

- **EnvSettingsModal 의 atoms (Section/Field/Input/Select/Toggle/ModeButton) 통째 제거** —
  Plan 카드는 "form 부분 이관" 만 명시했지만, atoms 가 EnvironmentEditor 안의
  것과 100% 중복이라 남겨두면 향후 스타일 drift 가 확정됨. 1192 → 588 LOC
  축소. Subdomain 가이드 안에서 쓰던 `Section` 만 `GuideSection` 으로 inline 유지.
- **Plan 의 "raw JSON 토글" 명시적 거부 결정 유지 + 한 단계 더** — extras 패널
  도 read-only 자체로는 JSON 우회 경로가 아니지만 chip 단위 삭제만 허용하도록
  설계 (자유 편집 X). "JSON 으로 도망갈 수 있게 만들면 H.1 검증을 우회" 의도 보강.
- **EnvSettingsModal 의 `useEffect(setForm)` 추가** — Plan 에는 없던 새 동작이지만
  모달이 환경 사이를 hop 할 때 stale form 이 보이던 잠복 버그가 같은 PR 에서
  드러나 즉시 fix. 별도 plan 카드 만들 가치 없음.
- **react-refresh 경고 3건 의도적 무시** — `defaultsFor`, `readForm`, `writeForm`
  helper 가 EnvironmentEditor.tsx 안에서 함께 export 되는 게 자연스러움. 파일
  분리는 가치 < 비용. (lint 통계: 새 파일에서 0 error, 기존 115 errors 는 pre-existing.)
