# M0-P2: 격리 + SeaweedFS PoC — 진행 기록

> Plan: [`../../plan/m0/p2_isolation_seaweedfs.md`](../../plan/m0/p2_isolation_seaweedfs.md)
> Status: **in_progress**
> Started: 2026-05-22
> Owner: gkfua00 (CocoRoF)
> Depends on: ✅ M0-P1 (commit `a4de305`)

## 진입 조건 검증

- [x] M0-P1 통과 (모노레포 + CI 그린, compose smoke 자체 검증 완료)
- [x] **sysbox-runc 0.6.7 설치됨** (`/usr/bin/sysbox-runc`)
- [x] sysbox / sysbox-mgr / sysbox-fs systemd unit 모두 `active`
- [x] `/etc/docker/daemon.json`의 `runtimes.sysbox-runc` 등록 + dockerd 로드
- [x] 실 검증: `docker run --rm --runtime=sysbox-runc hello-world` 정상 출력
- [x] Linux kernel 6.17.0-14-generic (≥ 5.10 요구)
- [x] cgroup v2 (cgroup2fs)
- [x] 호스트 디스크 ≥ 30 GB (801 GB free)
- [x] Docker Engine 29.2.1 + compose v5.1.0
- [x] 사용자 `docker` 그룹 가입
- [ ] 검증용 외부 git 레포 1개 — *PR 4 진입 시점에 사용자가 지정* (소형 compose 프로젝트, 예: hello-world API)

## PR 진행 로그

### PR 1 — poc/sysbox_isolation/ 골격 + 기본 sysbox 부팅 검증 (작성 완료, commit 대기)
- [x] `runtime/Dockerfile` 베이스를 `debian:bookworm-slim` → `ubuntu:24.04` (noble)로 변경 — Debian bookworm은 Python 3.12 미가용
- [x] PEP 668 (externally-managed-environment) 대응 — `--system` pip install 대신 `/opt/gapt-runtime-venv` 격리 venv로 데몬 설치 + `/usr/local/bin/toolkit-agent` 심볼릭
- [x] Docker apt repo URL: `debian/gpg` → `ubuntu/gpg`, 경로도 ubuntu로
- [x] `poc/sysbox_isolation/boot_sysbox.sh` — `gapt/runtime:dev` 빌드 (없으면) + Sysbox 컨테이너 부팅 + inner dockerd 헬스 대기 (≤30s)
- [x] `poc/sysbox_isolation/check_basic_isolation.sh` — B1~B5 검증 스크립트
  - B1: host `/var/run/docker.sock` 마운트 안 됨 — T6 차단 ✓
  - B2: inner Server Version (29.5.2) ≠ host (29.2.1) — 별개 dockerd ✓
  - B3: runtime=sysbox-runc ✓
  - B4: inner `docker ps -a` empty ✓
  - B5: 도구 인벤토리 git/gh/python3.12/node22/pnpm/uv/toolkit-agent/inner-docker 모두 ✓
- [x] `poc/sysbox_isolation/teardown_sysbox.sh` — 컨테이너 정리
- [x] `poc/sysbox_isolation/README.md` — 사용법 + B1~B5 결과 매트릭스 + 다음 PR에서 다룰 항목
- [x] **검증 통과**: teardown → boot → check → teardown 전체 사이클 그린, inner dockerd 1초 만에 응답
- *commit 대기*: `feat(poc): sysbox sandbox boot + basic isolation checks (M0-P2 PR1)`
### PR 2 — SeaweedFS 단일 노드 + S3 + volume driver 결정 (대기)
### PR 3 — Sysbox + SeaweedFS Mount 통합 (대기)
### PR 4 — inner dockerd로 외부 repo compose up (대기)
### PR 5 — 격리 검증 I1~I9 자동 테스트 (대기)
### PR 6 — SeaweedFS git 성능 측정 + decision docs (대기)

## DoD 진행

[Plan 카드](../../plan/m0/p2_isolation_seaweedfs.md)의 DoD 그대로 트래킹:

- [ ] `poc/sysbox_isolation/` 디렉토리에 모든 산출물
- [ ] **9개 격리 검증 시나리오 I1~I9** 모두 자동 테스트로 통과
- [ ] Sysbox 컨테이너 안에서 `git clone <외부 repo>` + `docker compose up -d` 정상 동작
- [ ] **호스트 `/var/run/docker.sock`이 컨테이너 어디에도 마운트되지 않음** (script 검증)
- [ ] SeaweedFS Mount 위 git clone 동작 + 워크스페이스 영속화 확인
- [ ] SeaweedFS Mount 위 git 명령 성능 측정 결과 기록
- [ ] CI에서 격리 검증 시나리오 자동 실행 (셀프호스트 runner 또는 manual workflow)

## Drift (cycle 종료 시 작성)

*(아직 종료되지 않음)*
