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
- ✅ `befec6a` feat(poc): sysbox sandbox boot + basic isolation checks (M0-P2 PR1)

### PR 2 — SeaweedFS 단일 노드 + S3 + volume driver 결정 (✅ 완료)
- [x] `seaweedfs.compose.yml` + `s3.poc.json` — 단일 SeaweedFS, 포트 19333/18888/18333 (dev stack과 +10000 shift)
- [x] `boot_seaweedfs.sh` / `teardown_seaweedfs.sh` (--wipe 플래그)
- [x] `check_seaweedfs.sh` — F1~F3 모두 PASS:
  - F1: Filer HTTP PUT/GET/DELETE round-trip ✓
  - F2: boto3 (`uv run --with boto3`) S3 round-trip (create_bucket/put/get/list/delete) ✓ — `BucketAlreadyExists`도 catch하여 idempotent
  - F3: 컨테이너 restart 후 영속성 (filer endpoint 자체 polling으로 fix) ✓
- [x] `decision_volume_driver.md` — **결정: 옵션 B (컨테이너 내부 `weed mount` FUSE)** M0~M2. 옵션 A (CSI)는 M4 K8s 단계에서 재검토. 사용 시 entrypoint sketch 동봉
- [x] `perf_seaweed_vs_host.md` — PR6 측정 결과 placeholder (스키마 미리 정의)
- [x] PoC `README.md` — F1~F3 결과 매트릭스 + decision/perf 문서 인덱스 추가
- ✅ `4f9b229` feat(poc): SeaweedFS PoC + S3 round-trip + volume driver decision (M0-P2 PR2)
### PR 3 — Sysbox + SeaweedFS Mount 통합 (작성 완료, commit 대기)
- [x] `runtime/Dockerfile`에 `fuse3` 패키지 + SeaweedFS `weed` 클라이언트 (v3.99) 동봉
- [x] `runtime/scripts/entrypoint.sh`에 `mount_seaweedfs_workspace()` 추가:
  - `GAPT_SEAWEED_FILER_URL` env 있으면 `/workspace`에 `weed mount` FUSE
  - URL → host:port 변환 (`-filer`는 URL이 아닌 host:port만 받음)
  - 백그라운드 spawn + 최대 15초 mountpoint polling
  - mount 실패해도 컨테이너는 계속 (로그에만 기록)
- [x] `boot_sysbox.sh` + `boot_integrated.sh`에 `--device /dev/fuse:/dev/fuse` 추가 — Sysbox 컨테이너에 FUSE 디바이스 노출
- [x] `boot_integrated.sh` — SeaweedFS + Sysbox 함께 부팅 + 같은 docker network + filer 사전 path 생성
- [x] `check_integration.sh` — I1~I4:
  - I1: `/workspace = fuseblk` filesystem ✓
  - I2: sandbox write → host Filer 읽음 ✓
  - I3: 컨테이너 restart 후 영속 ✓
  - I4: `git clone --depth 1 octocat/Hello-World` 30 files + host Filer가 디렉토리 listing ✓
- [x] `teardown_integrated.sh` (--wipe 옵션)
- [x] **검증 통과**: 4/4 I1~I4 PASS, mount 표시 `seaweedfs:8888:/projects/poc/workspaces/w1 fuse.seaweedfs 797G`
- [x] PoC README에 PR3 결과 매트릭스 + integration script 인덱스 추가
- *commit 대기*: `feat(runtime,poc): SeaweedFS FUSE mount in Sysbox sandbox (M0-P2 PR3)`
### PR 4 — inner dockerd로 외부 repo compose up (partial — KI-1 deferred)
- [x] `poc/sysbox_isolation/sample-compose/docker-compose.yml` — `hashicorp/http-echo` 작은 user-style compose (non-privileged 5678)
- [x] `runtime/scripts/entrypoint.sh` — dockerd 부팅에 `--storage-driver=fuse-overlayfs|vfs` 자동 fallback 로직 추가
- [x] `runtime/Dockerfile`에 `fuse-overlayfs` 패키지 추가 시도 — *edit 적용 안 됨* (다음 cycle에서 재시도, vfs fallback이 작동하므로 영향 없음)
- [x] `check_inner_compose.sh` — C0~C4:
  - C0: inner dockerd 응답 + `docker pull hashicorp/http-echo` 성공 ✓
  - C1: **XFAIL** — `docker run/compose up`이 runc init에서 `unsafe procfs (ip_unprivileged_port_start cross-device link)` 거부. docker 25+ 신 동작 vs Sysbox 0.6.7 비호환 ([`known_issues.md` KI-1](../../../poc/sysbox_isolation/known_issues.md))
  - C2: skipped — C1 미통과로 inner service 없음
  - C3: ✅ 호스트 docker에 inner-side image 0개 (격리 본질 작동)
  - C4: ✅ compose down idempotent
- [x] `known_issues.md` 작성 — KI-1 (docker 25+ procfs vs Sysbox) + KI-2 (`docker cp` over FUSE)
- [x] PoC `README.md`에 PR4 결과 + known_issues 참조 추가
- *plan §5 DoD "compose up 정상 동작"은 KI-1 해결 시까지 deferred* — M1-E1 cycle 1.7 SandboxBackend 진입 전 후속 cycle 필요
- *commit 대기*: `feat(poc): inner dockerd verified up to image pull; compose up deferred per KI-1 (M0-P2 PR4)`
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
