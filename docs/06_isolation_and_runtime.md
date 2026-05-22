# 06. 격리 / 런타임 (Isolation & Runtime)

> **상위**: [03](03_system_architecture.md)
> **다음**: [07_cicd_and_preview.md](07_cicd_and_preview.md)

이 문서는 GAPT의 **보안의 1번 원칙**을 구현하는 격리 레이어(`D4 Sandbox Controller`)와 컨테이너 런타임을 정의한다. *왜 Sysbox인가*, *호스트 docker 소켓 마운트를 절대 안 하는 이유*, *DinD가 안전하게 가능한 형태*, *프로젝트 컨테이너의 라이프사이클·리소스·네트워크·파일 시스템*을 다룬다.

핵심 결정 7개:

1. **Sysbox runtime 1차** — runc + user-namespace + 가상화 일부로 *안전한 DinD*.
2. **호스트 `/var/run/docker.sock` 마운트 금지** — 어떤 모드에서도.
3. **프로젝트당 1개 Sysbox 컨테이너** — 그 안에서 사용자 `docker compose up`이 *진짜로* 동작.
4. **gVisor를 *one-shot* 보조 격리에** (LLM이 한 줄 평가 등). M3+ 옵션.
5. **Kata/Firecracker는 SaaS 멀티테넌시 단계에서만**. M4+ 옵션.
6. **네트워크 egress 정책**은 *프로젝트별*. 기본 허용, 화이트리스트 모드 토글.
7. **컨테이너 이미지는 `gapt/runtime:latest`** — git/gh/uv/node/docker CLI/toolkit-agent 데몬 포함.

---

## 6.1 위협 모델 (Threat Model)

GAPT 안에서 *실행되는 코드*의 위협:

| 위협 | 출처 | 영향 |
|---|---|---|
| T1. LLM이 임의 셸 명령 실행 | Stage 10 Tool (Bash) | 컨테이너 RCE, 다른 프로젝트 침범 시도 |
| T2. 사용자가 직접 임의 명령 (xterm) | 데몬 PTY | 동일 |
| T3. 의존성 설치 시 악성 패키지 | npm/pip/uv install | 동일 |
| T4. git submodule URL 변조 | 외부 레포 | 동일 |
| T5. 사용자 코드의 compose가 `:/host`를 마운트 | compose.yml | 호스트 FS 노출 |
| T6. 컨테이너 안에서 호스트 docker.sock 사용 | LLM 명령 | **호스트 root 탈취** |
| T7. cgroup 우회로 리소스 고갈 | 임의 코드 | DoS |
| T8. 네트워크로 사내망 스캔/공격 | 임의 코드 | 외부 자원 공격 |
| T9. 시크릿 평문 추출 (`env`, `cat /proc/*/environ`) | LLM/사용자 | 토큰 누출 |
| T10. 컨테이너 이미지 자체에 백도어 | 공급망 | 모든 컨테이너 컴프로마이즈 |

**T6**이 가장 치명적이고, *바로 그것* 때문에 Sysbox + 호스트 소켓 비-노출이 본 toolkit의 기둥이다.

---

## 6.2 격리 옵션 비교 (재게재 + 우리 컨텍스트)

| 기술 | 격리 강도 | 시작시간 | 오버헤드 | DinD | 적합도 |
|---|---|---|---|---|---|
| **순수 Docker (runc)** | 약 (공유 커널) | <100ms | 거의 0 | privileged 필요 (위험) | ❌ |
| **Sysbox** (Nestybox→Docker) | 중상 (user-ns + 일부 가상화) | ~200ms | 적음 | **안전한 DinD 1급** | **★ 1차** |
| **gVisor** (Google) | 상 (유저스페이스 커널) | ms | 중간 (syscall 가로채기) | DinD 안 됨 | one-shot |
| **Kata Containers** | 최상 (VM) | 150-300ms | 메모리 ↑ | 가능 | SaaS 단계 |
| **Firecracker microVM** | 최상 (VM) | 100-200ms | 중간 | OCI 어댑터 필요 | E2B 스타일 시 |

### 6.2.1 왜 Sysbox

1. **사용자 시나리오 = `docker compose up`**: 사용자 compose 파일을 *수정 없이* 그대로 굴려야 한다. 그러려면 컨테이너 안에 진짜 dockerd가 있어야 한다.
2. **호스트 socket 마운트 회피**: privileged + `/var/run/docker.sock` 마운트는 *호스트 root 등가*. Sysbox는 이를 회피하면서 같은 결과(컨테이너 내 dockerd) 제공.
3. **운영 단순성**: K8s 없이 호스트에 sysbox-runc만 설치하면 됨. Coolify가 2GB VPS에서 도는 정신과 일치.
4. **라이선스**: Apache 2.0 (커뮤니티 에디션).
5. **검증 사례**: Coder가 워크스페이스 격리에 Sysbox 권장. Docker Hardened Desktop의 ECI도 Sysbox 기반.

### 6.2.2 gVisor의 역할

Sysbox는 *프로젝트 컨테이너* 격리에 1순위지만, gVisor는 *one-shot 평가*에 적합:

- LLM이 "이 Python 스니펫을 실행해서 결과만 봐줘" 요구 시
- 외부 코드 스니펫(예: GitHub Gist) 안전 실행 시
- M3+에서 보조 격리로 도입.

### 6.2.3 Kata/Firecracker

- **언제**: 멀티 테넌트 SaaS 단계 (M4+). 같은 호스트의 여러 사용자가 *서로의 컨테이너에 접근 못 하게* VM 단위 격리가 필요해질 때.
- **지금**: 단일 사용자/소규모 팀이라 Sysbox로 충분. 인터페이스(`SandboxBackend`)가 향후 구현체 교체를 허락하도록 설계.

---

## 6.3 Sysbox 컨테이너 구조

```
[Sysbox Container]
├─ user namespace: 호스트 root != 컨테이너 root
├─ root filesystem (OverlayFS, COW)
│   ├─ /usr/bin/docker (CLI)
│   ├─ /usr/bin/dockerd (Sysbox가 안전하게 실행 허용)
│   ├─ /usr/bin/git, /usr/bin/gh
│   ├─ /usr/local/bin/toolkit-agent (Python 데몬 ~5MB)
│   └─ /workspace (사용자 코드, **SeaweedFS Mount** — 영속 파일은 무조건 SeaweedFS)
├─ inner dockerd
│   ├─ 컨테이너 안의 docker 명령 = inner dockerd에 연결
│   ├─ 사용자 compose stack 여기서 실행
│   └─ 호스트 dockerd에 *전혀* 접근 안 함
├─ resource limits (cgroup v2)
│   ├─ --memory, --memory-swap, --cpus, --pids-limit
│   └─ network: 프로젝트별 docker network
└─ secrets (단명 주입)
    ├─ 환경변수: 세션 수명 동안만
    ├─ 파일: tmpfs 마운트 (/run/secrets)
    └─ git askpass socket
```

### 6.3.1 부팅 시퀀스

```
1. Sandbox Controller가 호스트 dockerd에 다음 요청:
   docker run -d \
     --runtime=sysbox-runc \
     --name gapt-{project_slug}-{workspace_id} \
     --memory 4g --memory-swap 4g \
     --cpus 2 --pids-limit 4096 \
     --network gapt-{project_slug} \
     --mount type=volume,source=gapt-seaweed-{id},target=/workspace,volume-driver=seaweedfs \
     -v gapt-docker-{id}:/var/lib/docker \
     -e GAPT_DAEMON_TOKEN={short-lived-jwt} \
     -e GAPT_PROJECT_ID={pid} \
     -e GAPT_WORKSPACE_ID={wid} \
     gapt/runtime:latest \
     /usr/local/bin/toolkit-agent serve --socket /run/agent.sock

2. 컨테이너 내부: toolkit-agent가 시작, inner dockerd도 systemd-less 부팅. `/workspace`는 SeaweedFS Filer를 도커 볼륨 플러그인(seaweedfs/seaweedfs-csi-driver) 또는 컨테이너 내 FUSE mount로 attach
3. 컨트롤 플레인이 데몬 헬스체크 (~5s 대기)
4. 데몬: git clone (이미 SeaweedFS에 마운트된 worktree 사용 — 기존 워크스페이스면 즉시 보임)
5. 데몬: compose stack 부팅 (사용자 compose.dev.yml)
6. Caddy에 subdomain 등록 → 외부 노출
```

이 모든 단계가 *audit*되고, 실패는 *명시적*. "조용히 계속" 없음.

### 6.3.2 컨테이너 자원 매핑 — *영속은 SeaweedFS*

**원칙**: 영속 파일 데이터는 *무조건* 동봉된 SeaweedFS에. host FS bind는 *캐시/임시* 한정.

| 출처 | 컨테이너 매핑 | 비고 |
|---|---|---|
| **SeaweedFS Mount** `seaweedfs://gapt/workspaces/{id}` | `/workspace` FUSE | **사용자 코드 영속 — 단일 추상화.** 백업/스냅샷이 SeaweedFS 메커니즘으로 통일 |
| **SeaweedFS Mount** `seaweedfs://gapt/uploads/{id}` | `/workspace/.gapt/uploads` | 사용자 LLM 첨부, 산출물 |
| 호스트 named volume `gapt-docker-{id}` | `/var/lib/docker` | inner dockerd overlay2 — *캐시성*, FUSE 위 overlay는 성능/무결성 위험으로 호스트 OK |
| 호스트 named volume `gapt-cache-{lang}` | 언어별 caches | npm/uv/cargo cache — 캐시성, 호스트 OK |
| (없음) | `/run/secrets/*` tmpfs | 시크릿 평문, 컨테이너 종료 시 휘발 |
| (없음) | `/run/agent.sock` mounted unix socket | 컨트롤 플레인 ↔ 데몬 |
| `/var/run/docker.sock` | **마운트 안 함** | T6 회피 |

두 가지 한 줄로:
- **호스트 docker 소켓을 *컨테이너 어디에도* 노출하지 않음** (T6).
- **영속 파일은 host FS bind가 아닌 SeaweedFS Mount로 들어옴** (단일 추상화).

호스트 named volume이 남는 이유는 *캐시/임시*인 경우 한정 — overlay2 같은 *블록 단위* 쓰기가 SeaweedFS FUSE 위에선 성능/무결성 모두 위험하기 때문. 캐시는 *깨져도 다시 빌드*되므로 호스트 OK.

### 6.3.3 사용자가 compose에 `/var/run/docker.sock`을 적으면

사용자의 compose가:
```yaml
services:
  some:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```
라고 적은 경우, 이건 *컨테이너 안의 dockerd 소켓*을 inner 서비스에 마운트한다 — *호스트의 소켓이 아님*. inner dockerd는 호스트와 격리되어 있으므로, 이 마운트가 호스트로 누수되지 않는다.

이게 Sysbox의 핵심 이점: 사용자가 *기존 패턴*을 쓸 수 있고, *결과적으로 안전*하다.

---

## 6.4 리소스 한계 (Resource Limits)

| 자원 | 기본 | 사용자 override |
|---|---|---|
| 메모리 | 4 GiB | 프로젝트 설정 가능 (호스트 80% cap) |
| Swap | 메모리와 동일 | — |
| CPU | 2 vCPU | 가능 |
| PIDs | 4096 | (DoS 방지) |
| 파일 디스크립터 | 65536 | — |
| 디스크 (worktree) | SeaweedFS volume — 프로젝트별 quota 가능 (Filer collection 단위) | (이미 1급) |
| 디스크 (inner docker volume) | 호스트 named volume — quota 없음 (M0~M2) | M3+ XFS project quota |

**호스트 전체 cap**: GAPT 컨트롤 플레인이 호스트 자원의 80%를 넘는 sandbox 합계를 만들지 않도록 boot 시 체크.

### 6.4.1 OOM 정책

- inner dockerd가 OOM되면 → 컨테이너 자체가 죽음 → Sandbox Controller가 자동 재시작 + 사용자 알림.
- LLM 도구 호출이 OOM 트리거하면 → Stage 10에서 `ToolExecutionError` → 세션 살리고 사용자에게 알림.

---

## 6.5 네트워크 정책

### 6.5.1 기본 토폴로지

```
[호스트]
├─ docker bridge: gapt-{project_slug}
│   ├─ 컨테이너 자신
│   └─ 사용자 compose의 서비스들 (같은 network)
└─ Caddy reverse proxy (호스트, 컨트롤 플레인 옆)
    └─ subdomain → 컨테이너 내부 포트 라우팅
```

각 프로젝트는 자기 docker network. 다른 프로젝트의 컨테이너와 직접 통신 불가.

### 6.5.2 외부 egress

| 모드 | 정책 | 적합도 |
|---|---|---|
| **Open** | 모든 egress 허용 | **기본 (M0)** |
| **Whitelist** | 화이트리스트 도메인만 (github.com, registry.npmjs.org, pypi.org, registry-1.docker.io, anthropic API, …) | 보안 우선 사용자 (M3+) |
| **None** | 외부 egress 차단 (사전 빌드된 이미지/소스만) | 에어갭 |

화이트리스트 구현: 컨테이너의 iptables/nftables를 데몬이 설정 (또는 호스트의 egress 프록시 경유).

### 6.5.3 내부 진입 (ingress)

- 사용자 compose가 `ports:`로 노출하는 호스트 포트는 *직접 노출 안 함*.
- Caddy가 `subdomain → 컨테이너 IP:port`로 reverse proxy.
- TLS는 Caddy의 on-demand TLS (호스트 도메인 + 와일드카드 인증서).

→ [07](07_cicd_and_preview.md)에서 자세히.

---

## 6.6 컨테이너 이미지 (`gapt/runtime`)

### 6.6.1 베이스

- **Debian slim** (12 / bookworm) — Alpine은 musl로 일부 Python 휠 빌드 깨짐.
- 멀티 스테이지 빌드로 최종 ~600MB (압축 ~250MB).

### 6.6.2 포함된 도구

| 카테고리 | 도구 |
|---|---|
| Shell / 기본 | bash, coreutils, less, vim-tiny, jq |
| Git | git 2.x, git-lfs, gh CLI 최신 |
| Docker | docker CLI, docker-compose v2 (compose plugin) |
| Python | python3.12, uv (pip 대체), pytest |
| Node | node 22 LTS, npm, pnpm, yarn |
| 빌드 | gcc, make, cmake, build-essential |
| 네트워크 | curl, wget, ca-certificates |
| GAPT 데몬 | toolkit-agent (Python wheel, ~5MB) |
| LLM CLI | claude (Claude Code CLI) — 옵셔널, 사용자 토큰 있어야 |

언어/스택별 *추가* 이미지를 옵션으로 (gapt/runtime-python:slim, gapt/runtime-node, gapt/runtime-fullstack). 기본은 fullstack.

### 6.6.3 공급망 보안 (T10)

- 베이스 이미지 hash pin.
- `dockerfile`은 OSS 공개, GitHub Actions에서 빌드 + cosign 서명.
- 사용자가 빌드 reproducibility를 자기 머신에서 검증 가능해야 함.
- 의존성: 매주 Renovate로 갱신, 보안 패치는 patch 릴리스로 즉시.

---

## 6.7 데몬 (toolkit-agent) 책임

컨테이너 안에서 도는 작은 Python 데몬. 컨트롤 플레인의 *원격 손*.

### 책임

- Unix socket에서 컨트롤 플레인의 요청 수신 (JWT 검증).
- 도구 명령 실행 (Read/Edit/Bash/Compose/Git/...).
- PTY 세션 관리 (xterm.js 양방향).
- inner dockerd 헬스/로그 수집.
- 단명 시크릿 주입/폐기.
- 파일 변경 감시 (compose watch / 직접 inotify).
- 자체 audit (도구 호출 → 컨트롤 플레인에 stream).

### 비-책임

- LLM 호출 (컨트롤 플레인에서만).
- DB 접근 (컨트롤 플레인에서만).
- 다른 sandbox와의 통신 (절대 금지).
- 외부 인터넷 직접 호출 (사용자 코드 실행 외).

### 인터페이스

```python
class AgentDaemonClient:  # 컨트롤 플레인 측
    async def exec_tool(self, sandbox_ref, tool: str, args: dict, ttl_s: int = 60) -> ToolResult: ...
    async def open_pty(self, sandbox_ref, shell: str = "bash") -> PtyHandle: ...
    async def inject_secret(self, sandbox_ref, env: dict, ttl: str) -> None: ...
    async def revoke_secret(self, sandbox_ref, names: list[str]) -> None: ...
    async def list_processes(self, sandbox_ref) -> list[ProcessInfo]: ...
    async def health(self, sandbox_ref) -> HealthState: ...
```

mTLS unix socket + JWT (짧은 ttl 회전).

---

## 6.8 라이프사이클 (Lifecycle)

### 6.8.1 상태 머신

```
[creating] ──(부팅 성공)──→ [running] ──(idle 30분)──→ [paused]
    │                          │                            │
    │                          │←──(사용자 재진입)──────────┘
    │                          ▼
    │                     [stopping] ──(graceful 종료)──→ [stopped]
    │                          │
    │                          └─(타임아웃)──→ [failed]
    ▼
[failed] ──(사용자 재시도)──→ [creating]
```

- **paused = 컨테이너 정지, 데이터 유지**. inner dockerd의 사용자 서비스도 정지. 호스트 자원 회수.
- **stopped = 컨테이너 삭제, 데이터(worktree, docker volume)는 보존**.
- **failed = 에러로 정지, 로그 보관, 사용자 액션 대기**.

### 6.8.2 좀비 정리

TickEngine 백그라운드(60s 간격):
- `paused` 상태가 7일 넘으면 `stopped`로.
- `stopped` 상태가 30일 넘으면 데이터 GC 후보로 (사용자 확인 필요).
- 컨트롤 플레인이 모르는 호스트 컨테이너 발견 (drift) → 알림 + 사용자 의도 확인.

### 6.8.3 워크스페이스/프로젝트 삭제 시

- 워크스페이스: sandbox stop + worktree 디렉토리 삭제 + 워크스페이스 행 archive.
- 프로젝트: 모든 워크스페이스 stop → 모두 삭제 → 프로젝트 행 archive + 30일 후 hard delete.
- *원격 git에는 절대 손대지 않음.*

---

## 6.9 사용자 경험 측면의 격리

격리가 *완벽해도 사용자 경험이 나쁘면 격리 자체를 끄려고 한다*. 다음을 보장:

- 컨테이너 부팅 시간 < 5초 (이미지 캐시된 경우).
- `git clone` 진행 표시 (라이브 로그 스트림).
- compose 부팅 실패 시 *명확한 한 줄 에러* + "어떻게 디버깅" 가이드 링크.
- 메모리/CPU 한계 도달 시 사용자에게 알림 + override 옵션.
- 컨테이너 내부 디스크 사용량을 UI 상태바에 라이브 표시.

---

## 6.10 테스트 가능성

격리는 *검증되어야* 신뢰된다. M1 끝에 다음 시나리오를 통과해야 함:

| # | 시나리오 | 기대 결과 |
|---|---|---|
| I1 | 컨테이너 안에서 `docker run -v /:/host alpine ls /host` | ✅ inner docker의 격리된 `/`만 보임, 호스트 X |
| I2 | 컨테이너 안에서 `cat /var/run/docker.sock` | ✅ 파일 없음 (마운트 안 됨) |
| I3 | 컨테이너 안에서 `ip addr` | ✅ 호스트 네트워크 인터페이스 미노출 |
| I4 | 컨테이너 안에서 fork bomb | ✅ pids cgroup으로 4096 이내 차단, 호스트 영향 없음 |
| I5 | 컨테이너 안에서 4GB+ 메모리 할당 | ✅ OOM kill, 호스트 OK |
| I6 | 컨테이너 안에서 `mount` | ✅ user namespace에 갇힘 |
| I7 | 컨테이너 안에서 `sysctl kernel.panic=1` | ✅ EPERM |
| I8 | 두 프로젝트 컨테이너 간 ping | ✅ 다른 docker network라 안 닿음 |
| I9 | 시크릿 환경변수가 `cat /proc/{daemon}/environ`에 평문 노출되는가 | ✅ 데몬이 명시적으로 마스킹/별도 위치에 저장 |

이 검증을 *CI에 자동화*하고 매 PR에서 회귀를 막는다.

---

## 6.11 운영 측면

### 6.11.1 호스트 사전요건

| 요구 | 비고 |
|---|---|
| Linux 커널 5.10+ | Sysbox 호환 |
| docker-ce 24+ | sysbox-runc 호환 |
| sysbox-runc 설치 | apt 패키지 또는 GitHub release |
| 호스트 메모리 ≥ 8GB | 권장 (멀티 프로젝트 위해) |
| 호스트 디스크 ≥ 50GB | SeaweedFS volume server + 호스트 캐시 (inner docker, npm/uv 등) |

설치 가이드는 별도(M0 출시 시 README). 호스트가 *부적합*하면 GAPT는 *부팅 거부* + 명확한 가이드.

### 6.11.2 Docker Desktop 함정 ([10](10_tech_stack_decisions.md))

Docker Desktop은 대기업 상용 라이선스. 우리는 *Linux 호스트 + Docker Engine 직접 설치*를 권장. macOS/Windows는 *지원하지 않음* — Linux VM 안에서만 운영.

### 6.11.3 Compose 운영 함정

[[feedback_sudo_compose_home_pitfall]]: prod compose가 `sudo`로 돌면 `$HOME=/root`. 우리는 이걸 *반복하지 않기 위해* 컨테이너 내부에서:
- 명시적 절대 경로 사용 (`${HOME}` 회피)
- GAPT 관리 영속 파일은 SeaweedFS Mount, 사용자 compose 자체 named volume은 inner dockerd가 관리
- 환경변수는 데몬이 명시적으로 주입

---

## 6.12 본 문서가 보장하는 인터페이스

1. **호스트 docker 소켓은 어떤 컨테이너에도 마운트되지 않는다.**
2. **모든 사용자 코드 실행은 Sysbox 격리 안에서 일어난다.**
3. **컨테이너 간 통신은 같은 docker network 안 같은 프로젝트 한정.**
4. **시크릿 평문은 호스트 FS 어디에도 영속화되지 않는다** (Vault 백엔드 외).
5. **부팅/정지/삭제는 모두 audit 이벤트 발행.**
6. **호스트 자원 합계의 80% 초과 sandbox 생성 거부.**
7. **격리 검증 시나리오(I1~I9) CI 통과는 릴리스 조건.**

이 보장들 위에서 [07](07_cicd_and_preview.md)이 *빌드/배포 파이프라인*과 *프리뷰 노출*을 정의한다.
