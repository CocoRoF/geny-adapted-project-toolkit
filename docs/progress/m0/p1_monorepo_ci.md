# M0-P1: 모노레포 셋업 + CI — 진행 기록

> Plan: [`../../plan/m0/p1_monorepo_ci.md`](../../plan/m0/p1_monorepo_ci.md)
> Status: **in_progress**
> Started: 2026-05-22
> Owner: gkfua00 (CocoRoF)

## 진입 조건 검증

- [x] 12편 분석 docs 통과 (사용자 검토 완료)
- [x] M0-P1 plan 카드 작성 + 사용자 검토 통과 ("좋아 진입하자")
- [x] git identity 설정 — `CocoRoF <gkfua00@gmail.com>`
- [ ] GitHub 레포 생성 — *PR 1 종료 후 사용자가 직접 생성 + remote add*
- [x] `uv` 0.4+, `pnpm` 9+, `docker` 24+ — *별도 사전조건 (사용자 머신)*

## PR 진행 로그

### PR 1 — 레포 부팅 파일 (작성 완료, commit 대기)
- [x] `git init -b main` 완료
- [x] LICENSE (Apache-2.0) — 표준 텍스트, 저작권 "CocoRoF and geny-adapted-project-toolkit contributors"
- [x] `.gitignore` — Python/Node/IDE/OS/secrets/SeaweedFS 데이터 디렉토리 포함
- [x] `.editorconfig` — 기본 LF + 4 space, JS/TS/YAML/JSON은 2 space
- [x] `README.md` — 한 줄 정의 + 시장 갭 표 + 9 원칙 + 12 docs 인덱스 + 의존 자원 + Apache-2.0 + Phase 0 상태
- [x] `CONTRIBUTING.md` — cadence 규칙 9개 절 (cycle 흐름, PR 본문 필수 필드, 머지 체크, 우리가 안 하는 것 등)
- *commit 대기*: 사용자 검토 후 `feat: bootstrap repo (M0-P1 PR1)` 머지 예정

### PR 2 — server/ 스켈레톤 (대기)
### PR 3 — runtime/ 스켈레톤 (대기)
### PR 4 — web/ 스켈레톤 (대기)
### PR 5 — compose/ dev 스택 (대기)
### PR 6 — CI workflows (대기)
### PR 7 — pre-commit + 품질 도구 (대기)

## DoD 진행

- [ ] `server/`, `runtime/`, `web/` 빈 패키지 빌드 통과
- [ ] GitHub Actions: lint + type-check + test 그린
- [ ] `compose/docker-compose.dev.yml` 부팅 + 5 서비스 헬스체크 통과
- [ ] README + LICENSE + CONTRIBUTING
- [ ] pre-commit 훅 활성
- [ ] PR 템플릿 (plan/progress 참조 필드)

## Drift (cycle 종료 시 작성)

*(아직 종료되지 않음)*
