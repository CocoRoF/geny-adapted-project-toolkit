# 07. CI/CD 와 프리뷰 (CI/CD & Preview)

> **상위**: [03](03_system_architecture.md) / [06](06_isolation_and_runtime.md)
> **다음**: [08_web_ide_ux.md](08_web_ide_ux.md)

이 문서는 GAPT의 **빌드/배포 오케스트레이션**(`D6 Build/Deploy Orchestrator`)과 **라이브 프리뷰** 시스템을 정의한다. inner/outer loop 구분, GitHub Actions 위임 vs Woodpecker 임베드, 사용자 인프라 배포(local/SSH/webhook/K8s), Caddy subdomain 프리뷰, 외부 공유 옵션을 다룬다.

핵심 결정 7개:

1. **Inner loop ≠ Outer loop** — 컨테이너 내부 *변경→재기동* (dev) vs git push → CI/CD (prod).
2. **Outer loop의 1차 = GitHub Actions 위임**. Woodpecker 임베드는 M3+ 옵션.
3. **Inner loop = Compose Watch + 파일 inotify** — 사용자 dev 경험.
4. **배포 타깃 어댑터화**: local / remote-ssh / webhook / k8s (M4+).
5. **Caddy + on-demand TLS + subdomain** — 프리뷰의 표준 경로.
6. **prod 배포는 항상 2-factor + 명시적 확인**.
7. **GitOps(ArgoCD/Flux)는 K8s 백엔드 도입 시 옵션, 코어 강제 아님**.

---

## 7.1 두 가지 루프

### Inner Loop — 컨테이너 안의 dev 사이클

```
[사용자 또는 LLM이 파일 편집]
    ↓
[데몬이 inotify로 감지]
    ↓
[해당 compose 서비스에 따라:]
    - 코드 변경 → 서비스 재시작 또는 hot-reload (compose watch sync+restart)
    - Dockerfile 변경 → 이미지 재빌드 → 컨테이너 재생성
    - 의존성 파일 변경 (package.json, pyproject.toml) → 빌드 후 재생성
    ↓
[프리뷰 URL이 자동 갱신, UI에 알림]
```

**기반**: `docker compose watch`가 2024부터 정식 명령. compose.yml에 `develop.watch:` 섹션 추가하면 자동 동작. 우리는:

1. 사용자 compose에 이미 `watch:`가 있으면 그대로 사용.
2. 없으면 우리가 *자동 감지하지 않음*. UI에서 "watch 활성화" 토글을 사용자에게 명시적으로 제안.
3. 토글 시 *우리가 패치 파일을 따로 두지 않고* `.gapt/compose.override.yml`을 만들고 사용자 동의 후 적용.

### Outer Loop — git push → CI/CD → deploy

```
[사용자 또는 LLM이 PR 머지 / push]
    ↓
[GitHub Actions 자동 실행 (사용자 레포의 .github/workflows/*)]
    ↓
[GAPT가 진행 polling / webhook 수신]
    ↓
[CI 그린 + 사용자 명시적 'Deploy' 클릭]
    ↓
[Build/Deploy Orchestrator가 대상 환경에 배포]
    ↓
[배포 검증 (smoke-test, health check)]
    ↓
[Audit + 사용자 알림 (옵션: Slack/Discord)]
```

**중요**: Outer loop의 *마지막 deploy 단계*는 우리가 *항상* 해야 한다 (사용자 인프라에 손이 닿는 단계). CI(테스트/빌드) 단계는 *위임*해도 된다.

---

## 7.2 Inner Loop 세부

### 7.2.1 watch 모드

`docker compose watch` 표준 동작:
```yaml
services:
  api:
    build: .
    develop:
      watch:
        - action: sync
          path: ./src
          target: /app/src
        - action: rebuild
          path: ./pyproject.toml
```

`sync`는 파일을 컨테이너에 복사만 (앱이 자체 reload), `rebuild`는 이미지 재빌드.

### 7.2.2 GAPT가 watch에 추가하는 것

- **파일 트리 UI 라이브 동기화** — 사용자가 폴더 트리에서 보는 파일이 LLM이 만든 파일을 즉시 반영.
- **컴파일/빌드 출력 캡처** — sync 후 첫 요청에서 에러 로그를 알림.
- **포트 health check** — compose watch 후 서비스가 다시 들어왔는지 30초 polling.
- **재시작 카운터** — 짧은 시간 N번 재시작하면 *루프 의심* 알림.

### 7.2.3 LLM이 watch를 끄거나 켤 수 있는가

가능. 단 도구 권한 `Compose(watch-toggle)`로 별도 분리. 사용자가 명시 허용 안 한 경우 LLM 자동 토글 거부.

### 7.2.4 watch 부재 시 fallback

사용자 compose에 watch가 없고 활성화도 거부하면:
- GAPT가 *파일 변경 감지만* 하고 사용자에게 "재시작" 버튼 노출.
- LLM의 도구 `ComposeRestart(service=api)` 호출로 명시적 재시작.

---

## 7.3 Outer Loop — CI/CD

### 7.3.1 옵션 비교 (재게재)

| 접근 | 셋업 비용 | 디버깅 | 사용자 학습 | M0 | M3+ |
|---|---|---|---|---|---|
| GitHub Actions 위임 | 0 (사용자 이미 씀) | GH UI | 0 | ★ | ★ |
| 단순 hook (git push → compose pull) | 매우 낮음 | 직관적 | 0 | (inner loop) | ★ |
| **Woodpecker 임베드** | 중 (run server+agent) | YAML 친숙 | 중 | — | ★ (옵션) |
| Drone | 중 | YAML | 중 | — | — |
| Tekton/Argo Workflows | 높음 | k8s CRD | 높음 | — | (K8s 단계만) |

### 7.3.2 1차: GitHub Actions 위임

사용자의 `.github/workflows/*.yml`이 *그대로* 동작. 우리는:

1. **트리거**: PR 생성/머지/푸시 시 GH가 자동 실행. 우리가 *유발*하지 않음 (그러나 채팅에서 LLM이 push를 트리거).
2. **모니터링**:
   - Webhook 가능하면: `workflow_run` 이벤트 수신, 즉시 UI 업데이트.
   - 아니면 `gh run list --branch X --limit 5` polling (10s 간격, 최근 N분만).
3. **로그**: `gh run view {id} --log` 스트림 → SSE로 UI에 라이브.
4. **재실행**: 사용자가 UI에서 "Re-run" → `gh run rerun {id}`.
5. **결과 ↔ 배포 연결**: 워크플로 이름 컨벤션 (`build-and-test.yml`, `deploy-prod.yml` 등) 또는 사용자가 설정에서 매핑.

GitHub Actions 외에 GitLab CI, Bitbucket Pipelines, Gitea Actions도 같은 어댑터 패턴.

### 7.3.3 2차 (M3+): Woodpecker 임베드

오프라인/에어갭 환경 또는 GH Actions 분 한도가 부담스러운 경우.

- Compose 옵션에 `woodpecker-server` + `woodpecker-agent` 추가.
- 프로젝트 레포에 `.woodpecker.yml` 있으면 사용.
- 격리: Woodpecker agent도 Sysbox 컨테이너 안에서 실행 (호스트 격리 유지).

라이선스: Apache 2.0. 단일 바이너리, <50MB RAM.

### 7.3.4 ArgoCD / Flux

K8s 배포 시에만 의미. M4+ K8s 백엔드 도입 시 옵션. *코어에 강제하지 않음*.

---

## 7.4 배포 (D6 Orchestrator)

### 7.4.1 환경 모델

```python
@dataclass
class Environment:
    id: str
    project_id: str
    name: str                              # "dev" | "staging" | "prod" | "custom"
    deploy_target: DeployTarget
    secret_refs: list[SecretRef]
    pre_hooks: list[Hook]                  # 백업, schema validate, etc.
    post_hooks: list[Hook]                 # smoke-test, slack notify
    require_2fa: bool                      # prod 기본 True
    cost_multiplier: float                 # LLM 작업의 cost cap을 더 엄격하게
```

### 7.4.2 DeployTarget 어댑터

```python
class DeployTarget(Protocol):
    async def deploy(self, ctx: DeployContext) -> DeployResult: ...
    async def status(self, ctx: DeployContext) -> DeployStatus: ...
    async def rollback(self, ctx: DeployContext, to: Version) -> RollbackResult: ...
```

구현체:

| Target | 무엇 | M0 | M3+ |
|---|---|---|---|
| `LocalComposeTarget` | 자기 호스트의 다른 sandbox 또는 컨테이너에 compose up | ✅ | ✅ |
| `RemoteSshTarget` | SSH로 원격 호스트에 compose 명령 | ✅ | ✅ |
| `WebhookTarget` | 사용자 정의 webhook (HMAC 서명) 호출 | ✅ | ✅ |
| `KubernetesTarget` | kubectl apply / helm upgrade | — | ✅ |
| `ArgocdTarget` | Argo sync 트리거 | — | ✅ |

### 7.4.3 LocalComposeTarget

가장 단순. 같은 호스트의 *prod-sandbox* (별도 Sysbox 컨테이너)에 deploy.

```
1. 사용자 명시 'Deploy to local-prod' 클릭
2. Secret Vault에서 .env.prod 평문 단명 조회
3. 데몬 RPC: target sandbox에 시크릿 주입
4. compose pull (새 이미지) → compose up -d
5. health check (사용자 정의)
6. 시크릿 폐기
7. audit 이벤트
```

빌드된 이미지가 어디 있는가:
- 옵션 A: 같은 호스트의 컨테이너 레지스트리 (compose에 빌드 정의)
- 옵션 B: 외부 레지스트리 (Docker Hub / ghcr.io / GitHub Container Registry)
- 옵션 C: 같은 sandbox 안에서 빌드 후 *별도* prod-sandbox에 export (M3+)

### 7.4.4 RemoteSshTarget

SSH 키 등록 → 원격 호스트에서 compose 명령 실행.

```
1. SSH 키 Vault 조회 (단명 ssh-agent)
2. ssh user@remote 'cd /apps/foo && docker compose pull && up -d'
3. ssh user@remote 'docker compose ps' health 체크
4. agent 종료 (SSH 키 폐기)
```

원격 호스트에 docker만 있으면 됨 (Kamal 정신).

### 7.4.5 WebhookTarget

사용자가 자기 인프라에 *어떤 형태로든* 배포 자동화를 갖고 있다면:

```
POST {webhook_url}
  X-GAPT-Signature: hmac-sha256={...}
  body: { project, env, version, image, ... }
```

원격 측에서 검증 후 자체 배포 스크립트 실행. 우리는 응답을 받아 결과 표시.

---

## 7.5 2-Factor & Approval

deploy 액션의 최종 결정은 [09](09_security_authz_observability.md) §9.2.3 **PolicyEngine** 이 한다. 다음은 **기본 정책 (default bundle)** — 사용자가 변경 안 하면 적용:

| 조건 | 기본 게이트 | config로 조정 가능? |
|---|---|---|
| prod 환경 deploy | 2FA (TOTP) | ✅ (강도 조절 또는 추가 가드 — 단 *deny → require_2fa* 같은 완화는 owner-only + audit) |
| LLM이 deploy.prod 도구 호출 | **기본 deny** (사용자 클릭만) | ✅ (예: `require_user_approval`로 완화, 또는 *완전 허용*도 owner가 책임지면 가능) |
| LLM이 deploy.dev/staging 도구 호출 | require_user_approval | ✅ |
| 직전 5분 내 다른 deploy 진행 중 | 큐잉 + 사용자 확인 | (불변) |
| 코드 변경 없이 같은 image 재배포 | 1-click | ✅ |
| schema 변경 포함 | dry-run 결과 사용자 확인 | ✅ |

> *원칙* ([[feedback_policy_config_not_hardcode]]): "LLM이 prod에 직접 손대지 못 한다"는 *기본값*이지 *코드에 박힌 절대 금지*가 아니다. 솔로 사용자/자동화 시나리오(P4)에서 책임지고 풀고 싶다면 PolicyEngine config로 가능. 단 *완화는 owner 권한 + 추가 확인 + audit*.

이 정신 덕에 (a) 솔로 P1이 야간 자동 PR-머지-배포 시나리오를 만들 수 있고, (b) 팀 P2가 더 엄격한 정책(예: 두 사람 승인)을 추가할 수 있다.

---

## 7.6 마이그레이션 / 스키마 변경

prod에 schema migration은 가장 위험한 작업. 게이트:

1. **dry-run 우선**: 자동으로 `--dry-run` 옵션 또는 별도 sandbox에서 staging DB 복제본에 실행.
2. **사용자에게 영향 받는 행 수 / 시간 추정 표시**.
3. **백업 hook 자동 실행** (pre-deploy hook으로 정의).
4. **롤백 스크립트 동봉 확인** — 없으면 deploy 거부.
5. **롤포워드 정책 명시**: "마이그레이션 실패 시 자동 롤백 OR 정지" 사용자 선택.

마이그레이션 도구는 사용자 스택 의존 (alembic / prisma / sqlx / sequelize / ...). GAPT는 *프로젝트 메타*에 "migration command" 필드 두고 호출만.

---

## 7.7 라이브 프리뷰

### 7.7.1 토폴로지

```
[브라우저]
   │ HTTPS
   ▼
[Caddy (호스트)]
   │ on-demand TLS, subdomain 라우팅
   ├─ toolkit.my-host.com   → 컨트롤 플레인 (FastAPI)
   ├─ geny.preview.my-host.com  → 프로젝트 컨테이너 IP:port (inner)
   ├─ geny-feat-x.preview.my-host.com  → 다른 워크스페이스
   └─ ...
```

### 7.7.2 Caddy 설정 패턴

```Caddyfile
{
    on_demand_tls {
        ask https://toolkit.my-host.com/internal/caddy/ask
    }
}

*.preview.my-host.com {
    tls { on_demand }
    @workspace {
        header_regexp host Host ^(?P<slug>[^.]+)\.preview\.my-host\.com$
    }
    reverse_proxy @workspace {
        to {http.regexp.host.slug}.internal:8080  # 내부 DNS는 컨트롤 플레인이 관리
        ...
    }
}
```

`ask` 엔드포인트로 컨트롤 플레인이 *현재 등록된 워크스페이스에 대해서만* 인증서 발급 허용.

### 7.7.3 와일드카드 도메인 (DNS)

- 가장 좋은 방식: 와일드카드 DNS 레코드 `*.preview.my-host.com → 호스트 IP`.
- 와일드카드 인증서: 사용자 선택
  - Let's Encrypt + DNS-01 (Cloudflare / Route53 / ...) — *완전 자동*.
  - 또는 on-demand TLS로 *요청 시마다* 발급 (TLS-ALPN-01).
- 사설/로컬: `*.preview.localhost` 또는 `*.preview.gapt.local` + 자체 CA.

### 7.7.4 외부 공유 (옵셔널)

사용자가 *프리뷰 URL을 외부에 공유*하고 싶을 때:

| 옵션 | 셋업 | 비용 | 적합도 |
|---|---|---|---|
| 호스트가 이미 public IP | DNS만 추가 | 0 | ★ |
| **cloudflared tunnel** | 1-click 토글 | 0 (CF 무료) | ★ |
| ngrok | 토큰 등록 | freemium | 보조 |
| Tailscale Funnel | 토큰 등록 | freemium | 보조 |

cloudflared가 가장 단순. *기본 OFF*, 사용자 명시 활성화.

### 7.7.5 인증

프리뷰 URL의 인증:
- *공개* (인증 없음) — 위험. 옵트인.
- *공유 링크* (서명된 URL, ttl) — 일반.
- *사용자 SSO* — 워크스페이스 멤버만 접근.

기본은 *사용자 SSO*. 공유 링크는 명시 생성.

---

## 7.8 빌드 캐시

다수 프로젝트가 *유사한 의존성*을 가지므로 캐시는 중요:

- **inner docker volume**(`gapt-docker-{id}`)이 프로젝트별 빌드 캐시 유지.
- 프로젝트 간 *공유 캐시*는 *기본 OFF* (격리 보장 우선). 사용자가 명시 활성화하면 `--cache-from` 패턴.
- npm/pnpm/uv/cargo cache는 *캐시성*이므로 호스트 named volume(`gapt-npm-cache` etc.) 공유 OK — 평문이긴 하지만 사용자 의존성은 어차피 공개, 깨져도 재빌드. (영속 파일은 SeaweedFS 원칙 [[06]] §6.3.2.)

빌드 시간 단축이 곧 *비용 절감*(LLM의 'compose up 기다리는' 시간 = 토큰 낭비). 캐시는 우선순위 ★★.

---

## 7.9 이미지 레지스트리

prod 배포에는 빌드된 이미지가 어딘가 *존재해야* 한다. 옵션:

| 레지스트리 | 셋업 | 비용 | 적합도 |
|---|---|---|---|
| **ghcr.io** (GitHub Container Registry) | 0 (GH 토큰 재사용) | 무료 (public) / 유료 (private) | **권장** |
| Docker Hub | 0 | 일부 무료 | 보조 |
| 자체 Registry (Distribution v2) | 컨테이너 1개 | 0 | 셀프호스트 가능 |
| AWS ECR / GCP AR | IAM 셋업 | 사용량 | 클라우드 |

빌드/푸시 명령은 사용자 CI에서. 우리는 *어느 레지스트리에서 pull할지*만 환경 메타데이터로.

---

## 7.10 롤백 / 재해 복구

| 시나리오 | 1차 액션 |
|---|---|
| Deploy 실패 (image pull fail) | 자동 중단, 이전 버전 유지, 사용자 알림 |
| Deploy 후 health check 실패 | 5분 자동 대기 → 사용자가 *명시적* rollback 결정 |
| Migration 실패 | 사용자가 정의한 롤백 스크립트 자동 실행 또는 정지 (사용자 선택) |
| 사용자 명시 rollback | 직전 또는 사용자 선택 version의 image로 compose up |

자동 rollback은 *기본 OFF*. 자동화는 *예측 가능*해야 한다.

---

## 7.11 알림 (Notification)

deploy 결과 / CI 결과 / 비용 cap 도달 등 알림 채널:

- **UI 안 토스트/알림 패널** (기본).
- **이메일** (transactional, SMTP 설정 필요).
- **Slack/Discord** (webhook URL 등록).
- **Telegram** (선택).
- **OS push** (PWA 활성화 시 web push).

알림 정책은 프로젝트 + 환경 단위로 설정. 시끄러움 방지를 위해 *기본은 보수적*.

---

## 7.12 비용 / 사용량 (CI)

- GitHub Actions 분 소모량을 `gh api` 통해 일별 집계 → UI 표시.
- Woodpecker 임베드 시 호스트 자원 사용량(컨테이너 metrics) 표시.
- LLM이 *루프*에 빠져 CI를 N번 트리거하면 경고 (rate limit으로 보호).

---

## 7.13 UI 통합

`08_web_ide_ux.md`로 자세히 위임. 본 문서가 가정하는 UI 요소:

- 워크스페이스 우측 패널에 *CI* 탭 (워크플로 라이브 로그).
- *Deploy* 탭 (환경 목록, 마지막 배포 시각, 1-click 트리거 + 2FA).
- *Preview* 탭 (URL, QR 코드, 외부 공유 토글).
- 좌측 트리에 워크스페이스별 *상태 점* (running / building / failed / deployed).

---

## 7.14 본 문서가 보장하는 인터페이스

1. **LLM의 deploy 자동 실행은 *기본 deny*** — PolicyEngine config에서 owner가 명시 완화 가능 + 추가 확인 + audit.
2. **모든 배포 대상은 `DeployTarget` 어댑터.** 직접 ssh/kubectl 호출 금지.
3. **시크릿은 단명 주입, 배포 후 즉시 폐기.**
4. **자동 rollback은 기본 OFF** — config로 활성화 가능.
5. **모든 deploy 시도는 audit 이벤트** (성공/실패/거부 모두) — 이건 *불변*.
6. **Caddy on-demand TLS는 등록된 워크스페이스에만 발급.**
7. **외부 공유 (cloudflared)는 기본 OFF + 사용자 명시 활성화.**

이 보장들 위에서 [08](08_web_ide_ux.md)이 *프론트엔드 IDE 셸*을 정의한다.
