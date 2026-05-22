# M0-P2: 격리 + SeaweedFS PoC — 진행 기록

> Plan: [`../../plan/m0/p2_isolation_seaweedfs.md`](../../plan/m0/p2_isolation_seaweedfs.md)
> Status: **done**
> Started: 2026-05-22
> Completed: 2026-05-22
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
### PR 4 — inner dockerd로 외부 repo compose up (✅ KI-1 resolved via Sysbox 0.7.0)
- [x] `poc/sysbox_isolation/sample-compose/docker-compose.yml` — `hashicorp/http-echo` 작은 user-style compose (non-privileged 5678)
- [x] `runtime/scripts/entrypoint.sh` — dockerd 부팅에 `--storage-driver=fuse-overlayfs|vfs` 자동 fallback 로직 추가 (Sysbox 0.7.0 + 호스트 docker-ce 29.2.1 환경에선 fallback 거의 안 탐 — 안전망)
- [x] `runtime/Dockerfile` docker-ce 버전 pin `5:29.2.1-1~ubuntu.24.04~noble` (host와 minor 정확 매칭 — deterministic)
- [x] `check_inner_compose.sh` — C0~C4 모두 PASS:
  - C0: inner dockerd 응답 + `docker pull hashicorp/http-echo` 성공 ✓
  - C1: ✅ **inner `docker compose up` 정상** (Sysbox 0.7.0 fix) — 첫 시도는 0.6.7 + docker 25+ procfs 충돌로 XFAIL이었으나 사용자가 upstream-published 0.7.0 deb (https://downloads.nestybox.com/sysbox/releases/v0.7.0/) 설치 후 통과
  - C2: ✅ inner http-echo 응답 (`curl 127.0.0.1:8089` → expected body)
  - C3: ✅ 호스트 docker에 inner-side image 0개 (격리 본질 작동)
  - C4: ✅ compose down idempotent
- [x] `known_issues.md` 업데이트 — KI-1 ✅ resolved 표시 + 해결 단서 (sysbox-fs commit `1302a6f`, sysbox 0.7.0 deb URL), KI-2는 유지
- [x] `check_integration.sh` I3 polling 30s → 90s (Sysbox 0.7.0 + weed mount cold remount latency 흡수) + fs type `fuse*` 확인 추가
- [x] PR3 회귀 검증: I1~I4 4/4 PASS (mount type / write→host / restart 영속 / git clone — Sysbox 0.7.0에서도 그대로)
- *commit 대기*: `feat(poc): KI-1 resolved by Sysbox 0.7.0 — inner compose up PASS (M0-P2 PR4)`
### PR 5 — 격리 검증 I1~I9 자동 테스트 (✅ 완료)
- [x] `poc/sysbox_isolation/tests/pyproject.toml` — pytest + pytest-timeout + ruff + mypy (별개 uv 가상환경, 호스트 sysbox 환경 의존)
- [x] `tests/conftest.py` — Sandbox 클래스 + `sandbox`/`sandbox_pair` fixture (UUID로 격리, fresh 컨테이너 자동 spawn/destroy) + docker/sysbox/image 가용성 자동 detect (skip with reason)
- [x] `tests/test_isolation.py` — I1~I9 9개 케이스:
  - I1: inner `docker run -v /:/host` 호스트 root marker 미노출 ✓
  - I2: 호스트 docker.sock mount source 부재 ✓
  - I3: 호스트 LAN IP가 컨테이너 netns에 없음 ✓
  - I4: `--pids-limit 256` cgroup이 400-process fork bomb 차단 ✓
  - I5: 512m memory.max에서 1 GiB bytearray alloc → SIGKILL ✓
  - I6: tmpfs mount가 호스트 /tmp에 marker leak 안 함 ✓
  - I7: sandbox의 `sysctl -w kernel.panic=99`이 호스트 kernel.panic 변경 안 함 ✓ (Sysbox 0.7.0의 procfs virtualization)
  - I8: 두 sandbox 컨테이너 ping 불가 (다른 docker network) ✓
  - I9: `/proc/1/environ`에 `GAPT_DAEMON_TOKEN` 평문 노출 — **xfail(strict=True)** (M1-E1 cycle 1.9 데몬 redaction 시까지)
- [x] `tests/README.md` — 사용법, 9 케이스 매트릭스, I9 xfail 의도 설명
- [x] `.github/workflows/isolation.yml` — manual dispatch + self-hosted runner (`sysbox` label) 워크플로. 일반 GitHub runner는 sysbox 없으므로 self-hosted only
- [x] **검증 통과**: `pytest -v` 8 PASS + 1 XFAIL, 49s, 모든 ruff/format clean
- ✅ `b5d0f4d` feat(poc): pytest-driven I1~I9 sandbox isolation matrix (M0-P2 PR5)

### PR 6 — SeaweedFS git 성능 측정 + decision docs (✅ 완료)
- [x] `poc/sysbox_isolation/bench_git.sh` — SeaweedFS + Sysbox 부팅 + `pre-commit/pre-commit` repo를 *3 위치*에 clone:
  - (a) `/workspace/bench-a` (SeaweedFS FUSE)
  - (b) `/var/lib/inner-bench/repo` (sandbox 자체 overlayfs)
  - (c) `/host-tmp/bench-c` (호스트 /tmp bind mount)
- [x] 5 git op × 20회 batch + bash builtin `time` (ms 해상도)
- [x] **측정 결과** (2026-05-22T15:27Z, host kernel 6.17 / sysbox-ce 0.7.0):

  | op | (a) FUSE | (b) overlay | (c) bind | a/c |
  |---|---|---|---|---|
  | status | 16 ms | 6 ms | 3 ms | **5.33x** |
  | log | 9 ms | 3 ms | 3 ms | **3.00x** |
  | diff | 10 ms | 8 ms | 4 ms | **2.50x** |
  | switch_back | 437 ms | 20 ms | 18 ms | **24.28x** ⚠️ |
  | commit_reset | 67 ms | 12 ms | 10 ms | **6.70x** |

- [x] **결정**: Option B (단일 FUSE mount /workspace) M0~M2 유지. read-only ops 2.5~6.7x slowdown은 single-digit ms 절대값으로 imperceptible. `git checkout` 24x (437 ms)는 sub-second flow-breaking 아님. hybrid layout (.git on host, worktree on FUSE)은 *복잡도 비용 > 이득* — M2 dogfood checkout cadence + M4 multi-node SeaweedFS 시점에 재평가
- [x] `perf_seaweed_vs_host.md` 완성: 결과 표 / Reading / Decision / Reproducing / Caveats
- [x] `decision_volume_driver.md` "What this does not settle" → "Settled by PR6 numbers" 절로 갱신, hybrid layout deferred 처리
- [x] `bench_git.sh` teardown: sandbox 안에서 `/host-tmp/bench-c` 먼저 rm (Sysbox user-ns remap으로 host가 못 지움 우회)
- *commit 대기*: `feat(poc): git perf measurement (FUSE vs overlay vs host bind) + decision update (M0-P2 PR6)`

## DoD 진행

[Plan 카드](../../plan/m0/p2_isolation_seaweedfs.md)의 DoD 그대로:

- [x] `poc/sysbox_isolation/` 디렉토리에 모든 산출물 (boot/check/teardown/bench × 4 흐름 + tests/ pytest + decision/perf/known_issues docs)
- [x] **9개 격리 검증 시나리오 I1~I9** 모두 자동 테스트로 통과 (8 PASS + 1 XFAIL의도)
- [x] Sysbox 컨테이너 안에서 `git clone <외부 repo>` + `docker compose up -d` 정상 동작 (Sysbox 0.7.0 fix + KI-1 closed)
- [x] **호스트 `/var/run/docker.sock`이 컨테이너 어디에도 마운트되지 않음** (I2 자동 검증)
- [x] SeaweedFS Mount 위 git clone 동작 + 워크스페이스 영속화 확인 (I3/I4)
- [x] SeaweedFS Mount 위 git 명령 성능 측정 결과 기록 (`perf_seaweed_vs_host.md`)
- [x] CI에서 격리 검증 시나리오 자동 실행 가능 — `.github/workflows/isolation.yml` 작성 (`workflow_dispatch` + self-hosted runner label `sysbox`. 실 runner 등록은 M1-E1 cycle 1.7로 위임)

## Commit 기록 요약

| commit | 주제 |
|---|---|
| `befec6a` | feat(poc): sysbox sandbox boot + basic isolation checks (PR1) |
| `4f9b229` | feat(poc): SeaweedFS PoC + S3 round-trip + volume driver decision (PR2) |
| `a855f37` | docs(progress): backfill PR1+PR2 SHAs |
| `afd1c2a` | feat(runtime,poc): SeaweedFS FUSE mount in Sysbox sandbox (PR3) |
| `e7ccef3` | style(poc): shellcheck SC2164/SC2034 + markdown emphasis (PR3 lint) |
| `f5423b4` | feat(poc): inner dockerd verified up to image pull, compose up deferred per KI-1 (PR4 v1) |
| `fb642e1` | feat(poc): KI-1 resolved by Sysbox 0.7.0 — inner compose up PASS (PR4 final) |
| `b5d0f4d` | feat(poc): pytest-driven I1~I9 sandbox isolation matrix (PR5) |
| _(pending)_ | feat(poc): git perf measurement + decision update (PR6) |
| _(pending)_ | docs(progress): close M0-P2 |

## Drift (plan ↔ 실제)

**plan 카드와 일치한 부분**:
- 6개 PR 단위 분할 그대로 (PR1 sysbox boot / PR2 SeaweedFS+decision / PR3 mount integration / PR4 inner compose / PR5 I1~I9 / PR6 perf)
- 9개 격리 시나리오 모두 자동화
- decision_volume_driver.md + perf_seaweed_vs_host.md 둘 다 산출

**plan에 없었지만 진행 중 발견·기록한 부분 (drift)**:
1. **Ubuntu 24.04 (noble) base 전환** — plan은 Debian bookworm-slim 가정이었으나 Python 3.12 미가용으로 Ubuntu noble로 전환. PEP 668 대응으로 daemon venv 격리.
2. **Sysbox 0.6.7 → 0.7.0 호스트 upgrade** — plan 진입조건엔 sysbox-ce만 명시. 실제로는 docker 25+ 호환을 위해 *unpublished 0.7.0 binary* (Nestybox upstream issue #973 thread)가 필수였음. 사용자가 deb install. 발견·해결 과정을 [`known_issues.md` KI-1](../../../poc/sysbox_isolation/known_issues.md)에 기록.
3. **docker-ce 정확 버전 pin** — host와 minor 차이가 Sysbox 0.6.7에서 문제였던 게 발견. 0.7.0에선 무관하지만 *deterministic build*를 위해 `5:29.2.1-1~ubuntu.24.04~noble` 유지.
4. **storage-driver fallback (fuse-overlayfs|vfs)** — Sysbox + bare-spawn dockerd 조합에서 기본 overlay 권한 거부 우회. 0.7.0에선 거의 안 타지만 안전망으로 유지.
5. **I3 polling 30s → 90s** — Sysbox 0.7.0 + weed mount cold remount latency가 30s를 종종 넘김. fs type `fuse*` 추가 검증.
6. **(c) host bind mount 자원 정리** — Sysbox user-namespace remap으로 호스트가 못 지움. teardown은 *sandbox 안에서* 먼저 rm.
7. **`docker cp` over FUSE 비신뢰성** ([`known_issues.md` KI-2](../../../poc/sysbox_isolation/known_issues.md)) — `tee` 우회.
8. **isolation workflow는 self-hosted runner 대기** — vanilla GitHub runner엔 sysbox 없으므로 manual dispatch + 실 runner 등록은 M1-E1 cycle 1.7.

**다음 cycle (M0-P3 — 에이전트 + MCP bridge PoC) 진입 전 사용자 검토 게이트**:
- [ ] 사용자가 `claude` CLI 최신 설치 + 인증 (OAuth subscription 또는 ANTHROPIC_API_KEY)
- [ ] M0-P2 결과물 검토 + 통과

검토 통과 후 M0-P3 진입.
