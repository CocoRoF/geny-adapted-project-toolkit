# M0-P2: 격리 + SeaweedFS PoC

> Status: planned
> Estimated: 5 작업일 / 6 PR
> Depends on: M0-P1
> Blocks: M0-P3, M1-E1
> Relates to: [`../../06_isolation_and_runtime.md`](../../06_isolation_and_runtime.md) (전체), [`../../03_system_architecture.md`](../../03_system_architecture.md) §3.1

## 목적 (한 줄)
**호스트 docker 소켓을 노출하지 않으면서** 사용자 compose가 *그대로* 동작하는 격리 컨테이너(Sysbox)를 부팅하고, 그 안에서 SeaweedFS Mount로 `/workspace`가 보이며, 외부 git 레포를 clone해 inner dockerd로 compose up이 끝까지 통과함을 *재현 가능하게* 보인다.

## 진입 조건
- [ ] M0-P1 통과 (모노레포 + CI)
- [ ] 호스트에 `sysbox-runc` 설치 ([공식 가이드](https://github.com/nestybox/sysbox/blob/master/docs/user-guide/install-package.md))
- [ ] 호스트 Linux 커널 5.10+
- [ ] 호스트 디스크 ≥ 30GB (SeaweedFS volume server + inner docker overlay)
- [ ] 검증용 외부 git 레포 1개 (작은 compose 프로젝트, 예: 임의 hello-world)

## DoD (Definition of Done)
- [ ] `poc/sysbox_isolation/` 디렉토리에 모든 산출물
- [ ] **9개 격리 검증 시나리오 I1~I9** ([06](../../06_isolation_and_runtime.md) §6.10) 모두 자동 테스트로 통과
- [ ] Sysbox 컨테이너 안에서 `git clone <외부 repo>` + `docker compose up -d` 정상 동작
- [ ] **호스트 `/var/run/docker.sock`이 컨테이너 어디에도 마운트되지 않음** (script 검증)
- [ ] SeaweedFS Mount 위 git clone이 동작 + 워크스페이스 영속화 확인 (컨테이너 재기동 후 데이터 유지)
- [ ] SeaweedFS Mount 위 git 명령 성능 측정 결과 기록 (status/log/diff/checkout — 호스트 FS 대비 배수)
- [ ] CI에서 격리 검증 시나리오 자동 실행 (GitHub Actions runner에 Sysbox가 없으므로 *셀프호스트 runner* 또는 *수동 트리거 워크플로*)

## 작업 항목 (세부)

### 1. SeaweedFS 단일 노드 부팅 검증
- `compose/seaweed/` 에 master+filer+volume+s3 단일 프로세스 + 디렉토리 영속 볼륨 설정
- `defaultReplication`: `000` (단일 노드, M0 한정), 운영 가이드에 *M4+ 멀티노드 전환* 메모
- Filer DB backend = `leveldb2` (단일 노드 적합) 또는 `postgres`로 통합
- `compose/seaweed/filer.toml` 디테일 작성
- 단위 테스트: S3 API로 `s3cmd ls s3://gapt`, mount API로 디렉토리 마운트 확인

### 2. SeaweedFS volume driver 선택
- 두 옵션 비교 + 결정:
  - (A) **`seaweedfs/seaweedfs-csi-driver` Docker plugin** — `docker plugin install ... weed-csi`, volume driver `weed`
  - (B) **컨테이너 내부 FUSE mount** — entrypoint에서 `weed mount -filer=... -dir=/workspace` 실행
- 1차 시도: (A) Docker plugin. 실패하면 (B)로 fallback (PoC 본질에는 영향 없음)
- 결과를 `poc/sysbox_isolation/decision_volume_driver.md`에 기록

### 3. Sysbox runtime 부팅 검증
- 호스트에 `sysbox-runc` 설치 가이드 + 검증 script (`poc/sysbox_isolation/install_sysbox.sh`)
- `docker run --runtime=sysbox-runc -d --name gapt-poc gapt/runtime:0.1 sleep infinity`
- 컨테이너 안에서 `dockerd` 자동 시작 (systemd-less, `dockerd` 직접 spawn)
- inner dockerd 헬스: `docker exec gapt-poc docker info`

### 4. SeaweedFS Mount + Sysbox 통합
- Sysbox 컨테이너 부팅 시 `/workspace`에 SeaweedFS 마운트
- 컨테이너 안에서 `df /workspace`로 SeaweedFS인지 확인
- 빈 디렉토리에 `git clone https://github.com/<small-test-repo>` → 파일이 SeaweedFS Filer에 저장됨을 확인 (`weed shell` 또는 S3 listing)
- 컨테이너 정지 → 같은 SeaweedFS volume으로 새 컨테이너 부팅 → `/workspace`에 같은 파일 보임

### 5. inner dockerd로 compose up
- 컨테이너 안에서 `cd /workspace/<repo> && docker compose up -d`
- 사용자 compose의 서비스가 *inner dockerd*에 부팅되는지 확인 (`docker exec gapt-poc docker ps`)
- *호스트 docker ps*에는 사용자 서비스가 *보이지 않음* (격리)
- 사용자 compose 안 hello-world 서비스의 포트를 컨테이너 외부로 expose하지 않고도 `curl localhost:<port>`가 *컨테이너 안에서* 동작
- 호스트에서 직접 그 포트로 접근하면 *안 닿음* — 외부 노출은 Caddy reverse proxy(M1-E3) 영역

### 6. 격리 검증 9개 시나리오 자동화
[06](../../06_isolation_and_runtime.md) §6.10의 표를 그대로 자동화:

```python
# poc/sysbox_isolation/tests/test_isolation.py
import subprocess, pytest

def exec_in(cmd: list[str], expect_fail: bool = False) -> str: ...

def test_i1_no_host_root_via_inner_docker():
    # 컨테이너 안에서 docker run -v /:/host alpine ls /host
    out = exec_in(["docker", "run", "--rm", "-v", "/:/host", "alpine", "ls", "/host"])
    # 호스트 root가 아닌 inner dockerd의 격리된 / 만 보여야
    assert "etc" in out and "kernel_specific_marker_not_present" not in out

def test_i2_no_host_docker_sock():
    out = exec_in(["test", "-S", "/var/run/docker.sock"], expect_fail=True)
    # ... etc I3~I9
```

I1~I9 모두 pytest로 작성, GitHub Actions에서 *셀프호스트 runner*에서 실행되도록 `.github/workflows/isolation.yml`.

### 7. SeaweedFS git 성능 측정
- 같은 레포를 (a) SeaweedFS Mount, (b) Sysbox 컨테이너 호스트 named volume, (c) 호스트 FS bind 세 가지에 두고 비교:
  - `git status` (변경 없을 때)
  - `git log --oneline -100`
  - `git diff HEAD~5`
  - `git checkout {branch}` (다른 브랜치)
  - `git add -A && git commit -m test`
- 각 5회 평균. 결과를 `poc/sysbox_isolation/perf_seaweed_vs_host.md`에 기록 + 임계 초과 시 어떤 작업이 SeaweedFS 위에 부적합한지 결론.
- *결론에 따라* M1-E1에서 worktree mount 전략 fine-tune (예: `.git` 디렉토리만 호스트, working tree는 SeaweedFS — 또는 그 반대).

### 8. 문서화
- `poc/sysbox_isolation/README.md` — 설치 가이드 + 부팅 + 검증 시나리오
- 진행하며 발견한 함정은 `analysis/2026XXXX_isolation_findings.md`에

## 산출물
```
poc/sysbox_isolation/
├── README.md
├── install_sysbox.sh
├── decision_volume_driver.md
├── perf_seaweed_vs_host.md
├── compose.poc.yml                 # PoC용 compose (host 외부)
├── runtime.Dockerfile              # poc 전용 runtime 이미지 (gapt/runtime:0.1)
├── scripts/
│   ├── boot_poc.sh                 # 호스트 1행 부팅
│   ├── teardown_poc.sh
│   └── check_no_host_sock.sh
└── tests/
    └── test_isolation.py           # I1~I9
.github/workflows/isolation.yml    # 셀프호스트 runner 매뉴얼 트리거
analysis/2026XXXX_isolation_findings.md  # 작업 중 작성
```

## 검증 시나리오
1. `bash poc/sysbox_isolation/install_sysbox.sh` → sysbox-runc 설치 + 검증 메시지.
2. `bash poc/sysbox_isolation/scripts/boot_poc.sh` → SeaweedFS + Sysbox 컨테이너 부팅, `/workspace`에 SeaweedFS 마운트, 외부 repo clone, inner compose up까지 90초 안에 완료.
3. `pytest poc/sysbox_isolation/tests/test_isolation.py -v` → 9/9 pass.
4. `bash poc/sysbox_isolation/scripts/check_no_host_sock.sh` → "host docker socket NOT exposed" 출력.
5. PoC 컨테이너 재시작 후 SeaweedFS 데이터 유지 (workspace 파일 보임).

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|
| `seaweedfs-csi-driver` Docker plugin 호환성 이슈 | 큼 — 마운트 전략 수정 | FUSE mount fallback ((B) 방안), `decision_volume_driver.md`에 기록 |
| SeaweedFS FUSE 위 git 성능이 비실용적 (예: status가 10× 느림) | 큼 — 04/06 전략 재검토 | hybrid 전략: `.git` 호스트 volume + working tree SeaweedFS, 또는 `.git` SeaweedFS + working tree 호스트 volume — 측정 결과로 결정 |
| Sysbox와 SeaweedFS Mount의 user namespace 충돌 | 큼 | Sysbox docs + GitHub issues 검색, mount option 조정 (`-o uid=...,gid=...`) |
| 호스트 dockerd가 cgroup v1 / v2 불일치 | 중 — 부팅 실패 | 사전 검증 script + 진입조건에 cgroup v2 명시 |
| GitHub Actions에 셀프호스트 runner 없음 | 중 — CI 회귀 안 됨 | 매뉴얼 트리거 + 사용자 PC에서 정기 실행 + 결과 progress에 기록. M1-E1 이전에 셀프호스트 runner 셋업 (별도 cycle 후보) |
| `--dangerously-skip-permissions` 같은 함정이 inner dockerd에서 우회 | 중 | PolicyEngine 게이트가 *컨테이너 외부*에서 미리 평가 (M1-E2) |

## 관련 docs
- [`../../06_isolation_and_runtime.md`](../../06_isolation_and_runtime.md) §6.2 격리 옵션, §6.3 컨테이너 구조, §6.10 검증 시나리오
- [`../../03_system_architecture.md`](../../03_system_architecture.md) §3.1 컨트롤/실행 플레인
- [`../../10_tech_stack_decisions.md`](../../10_tech_stack_decisions.md) §10.3 라이선스 함정 (L3 MinIO/SeaweedFS)
