# GAPT 개발 환경 셋업

> v1 단일-admin 가정. 다중 사용자 / OIDC 설정은 v1 범위가 아니므로
> 이 가이드는 단일 개발자가 본인 머신에서 GAPT 를 *상시 켜놓고*
> 쓰는 데 초점.

---

## TL;DR

```bash
# 한 번만:
cd ~/geny-workspace/geny-adapted-project-toolkit
docker compose -p gapt-dev -f compose/docker-compose.dev.yml up -d postgres redis seaweedfs caddy

# 매번 켤 때 (host uvicorn — 빠른 hot-reload):
./scripts/dev/server.sh start

# 종일 켜놓을 때 (재시작 자동 복구):
systemctl --user enable --now gapt-server   # 1회 설치 후 (아래 §4)
```

---

## 1. 컴포넌트 구조

```
호스트 OS (Linux)
├── docker compose 안에서 도는 것
│   ├── postgres        :35432  (control plane DB)
│   ├── redis           :36379
│   ├── seaweedfs       :38333 (S3) / :38888 (filer)
│   ├── caddy           :38080 (HTTP edge) / :32019 (admin)
│   └── prometheus      :39090 (optional `metrics` profile)
├── 호스트에서 직접 도는 것 (dev mode)
│   ├── uvicorn         :38001 (FastAPI 서버, hot-reload)
│   └── vite dev        :35173 (Web IDE SPA)
└── 외부 인프라
    └── cloudflared (systemd, /etc/systemd/system/cloudflared.service)
```

**왜 server + web 은 호스트에서 직접?**
- vite HMR + uvicorn `--reload` 가 코드 변경에 1초 이내 반응. 컨테이너 빌드 사이클 없이 즉시 검증 가능.
- compose 안에도 `server` 서비스가 정의돼 있지만 (prod 모드용), dev Caddyfile 은 `host.docker.internal:38001` 로 호스트 uvicorn 을 향한다 — 의도된 설정.

---

## 2. 데이터 플레인 + Caddy (compose)

```bash
cd ~/geny-workspace/geny-adapted-project-toolkit
docker compose -p gapt-dev -f compose/docker-compose.dev.yml up -d \
    postgres redis seaweedfs caddy
```

이 4개는 **상시 켜놓는 게 정상**. `restart: unless-stopped` 가 적용돼서 OS 재부팅 후에도 자동 복구.

상태 확인:
```bash
docker compose -p gapt-dev ps
curl -s http://127.0.0.1:32019/config/apps/http/servers | jq 'keys'  # caddy admin
```

### 2.1 Prometheus (옵션 — 외부 viz 붙일 때만)

Phase E.3 부터 dev compose 의 `prometheus` 는 `profiles: ["metrics"]`
뒤로 옮겨져 기본 부팅에 포함되지 않습니다. 서버의 `/metrics` 엔드포인트는
항상 살아있어서 (`curl http://127.0.0.1:38001/metrics`) 외부 scrape
도구를 직접 붙여도 됩니다.

성능 탭은 Prometheus 컨테이너에 의존하지 않습니다 — 서버 내부의
`MetricsRegistry` 를 직접 읽기 때문에 prometheus 가 안 떠 있어도
agent 비용/토큰이 그대로 표시됩니다.

내부에서 PromQL 쿼리가 필요할 때만:
```bash
docker compose -p gapt-dev -f compose/docker-compose.dev.yml \
    --profile metrics up -d prometheus
# 끄려면
docker compose -p gapt-dev -f compose/docker-compose.dev.yml \
    stop prometheus
```

---

## 3. GAPT 서버 (호스트 uvicorn)

### 옵션 A — 수동 (개발 중 잠깐 띄울 때)

```bash
./scripts/dev/server.sh start
./scripts/dev/server.sh status
./scripts/dev/server.sh logs        # tail -f
./scripts/dev/server.sh logs -n 50  # 마지막 50줄만
./scripts/dev/server.sh stop
./scripts/dev/server.sh restart
```

내부 동작:
- `nohup uv run uvicorn ... & disown` 으로 부모 shell 의존성 차단
- PID file: `/tmp/gapt-server.pid`
- 로그: `/tmp/gapt-server.log`
- 환경변수 default 는 `scripts/dev/server.sh` 상단 — 다른 값 쓰려면 invoke 시 `GAPT_X=Y ./server.sh start` 또는 영구 변경은 파일 수정

**왜 wrapper 가 필요한가**: `uv run uvicorn ...` 을 직접 터미널에서 띄우면 그 터미널이 닫히면 서버도 죽음. Claude Code 의 background-task 로 띄우면 task-stop 시 죽음. `nohup + disown` 으로 init (PID 1) 의 자식이 되도록 강제 → 노트북 절전, 셸 종료, Claude 세션 끝나도 살아남음.

검증:
```bash
ps -o pid,ppid -p "$(cat /tmp/gapt-server.pid)"
# PPID 가 1이면 daemonization 성공
```

### 옵션 B — systemd user unit (상시 켜놓을 때)

```bash
mkdir -p ~/.config/systemd/user
cp compose/systemd/gapt-server.service ~/.config/systemd/user/

# (선택) 환경변수 커스터마이즈가 필요하면 unit 파일 안 [Service] 의
# Environment= 라인들 편집

systemctl --user daemon-reload
systemctl --user enable --now gapt-server
systemctl --user status gapt-server
journalctl --user -u gapt-server -f
```

OS 재부팅 후에도 자동 시작하려면:
```bash
sudo loginctl enable-linger $USER
```

옵션 A 와 B 동시 사용 X — 둘 다 같은 포트 38001 점유 시도. systemd 쓸 거면 wrapper 의 `stop` 으로 먼저 정리 후 enable.

### 옵션 C — docker compose `server` 서비스

prod 시뮬레이션 / 컨테이너 환경 검증 시:
```bash
docker compose -p gapt-dev -f compose/docker-compose.dev.yml up -d server
```

그러면 Caddyfile.dev 의 `host.docker.internal:38001` 대신 compose 내부 `server:8088` 로 라우팅하도록 Caddyfile 도 같이 바꿔야 함 (`reverse_proxy server:8088`). 일상 개발에는 비추 — hot-reload 잃음.

---

## 4. Web IDE (호스트 vite)

```bash
cd web
pnpm install   # 한 번만
pnpm dev       # 35173 포트
```

vite 는 `host.docker.internal` 을 통해 Caddy `/_gapt/app/*` 로 통합. 외부에서 `https://gapt.hrletsgo.me/_gapt/app/` 접근.

---

## 5. 외부 접근 (Cloudflare Tunnel)

cloudflared 가 host:38080 (Caddy) 로 forward. systemd 로 상시 켜져 있어야 함:
```bash
systemctl status cloudflared
```

Tunnel ingress 변경은 GAPT 의 Settings → Providers → Cloudflare 에서 처리. CLI 직접 수정 비추 — GAPT 가 추적 못 함.

---

## 6. 문제 해결

### "외부 URL 이 502 / no response"
1. `./scripts/dev/server.sh status` — 서버 죽었는지 확인
2. 죽었으면 `./scripts/dev/server.sh start` 또는 `systemctl --user restart gapt-server`
3. 그래도 안 되면 cloudflared 재시작: `sudo systemctl restart cloudflared` (connector 4개 균등 분산 회복)

### "서버는 살아있는데 외부에서만 느림"
대개 cloudflared 가 connector 1개로 쏠림 — 위와 동일 (`sudo systemctl restart cloudflared`).

### "preview URL 이 GAPT 메인으로 리다이렉트됨"
스택 stop 후의 자연스러운 fallthrough (B.H.3 에서 catch-all 404 로 정리 예정). 무시.

### "Vault master key 분실"
새 마스터키로 띄우면 기존 암호화 secret 들 못 읽음. 토큰 재등록 필요.

---

## 7. 권장 구성

| 단계 | 컴포넌트 | 어떻게 |
|---|---|---|
| 노트북 데일리 개발 | postgres/redis/seaweedfs/caddy | docker compose (상시) |
| 노트북 데일리 개발 | server | systemd user unit (상시) |
| 노트북 데일리 개발 | web vite | `pnpm dev` (수동, 작업 중일 때만) |
| 노트북 데일리 개발 | cloudflared | systemd system (상시) |
| 빠른 디버깅 | server | `./scripts/dev/server.sh start` |
| 컨테이너 환경 검증 | server | compose `server` 서비스 (가끔) |

---

## 8. 자주 묻는 것

- **uv 가 없어요**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **docker compose 가 v1**: 이 repo 는 v2 (`docker compose`, 띄어쓰기) 가정. v1 (`docker-compose`) 은 deprecated.
- **포트 충돌**: GAPT 인프라는 전부 3xxxx prefix ([reference_gapt_port_convention](../memory/reference_gapt_port_convention.md)). 사용자 서비스가 5000 / 3000 / 8080 등을 쓰면 충돌 X.
