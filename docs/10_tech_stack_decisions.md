# 10. 기술 스택 결정 (Tech Stack Decisions)

> **상위**: [03](03_system_architecture.md) ~ [09](09_security_authz_observability.md)
> **다음**: [11_roadmap.md](11_roadmap.md)

이 문서는 GAPT의 **단일 결정 시트**다. 각 레이어에서 *어떤 기술을 선택했는지*, *왜인지*, *어떤 옵션을 거부했는지*, 그리고 *라이선스/비용 함정 체크리스트*를 한곳에 모은다. 후속 문서나 코드 리뷰가 결정의 근거를 즉시 추적할 수 있도록.

---

## 10.1 결정 매트릭스 한 페이지

| 레이어 | M0~M2 선택 | M3+ 진화 | 거부한 옵션 (이유) |
|---|---|---|---|
| **백엔드 언어/프레임워크** | Python + FastAPI + asyncio | (변화 적음) | Node/Hono (Claude Code 통합 약함), Go/Rust (생산성↓ SDK 약함) |
| **에이전트 엔진** | **geny-executor 2.1.0+ (PyPI 의존, manifest-driven)** | executor 자체 일반화 업그레이드 후 사용 | Fork (평행 진화 부담), 자체 작성 (재발명), GAPT 측 `LlmAgentBackend` Protocol조차 두지 않음 (이중 추상화 회피) |
| **LLM 1차 provider** | `claude_code_cli` (manifest `stages[6].config.provider`) + **host MCP wrap**으로 GAPT 도구 노출 | `anthropic` / `openai` / `google` / `vllm` 모두 manifest 편집으로 선택 가능 (이미 executor에 내장) | GAPT에 별도 어댑터 클래스 (executor 일반화로 흡수가 원칙) |
| **Pipeline 빌드 경로** | `EnvironmentManifest.load()` + `Pipeline.from_manifest_async()` | (변화 없음) | `PipelineBuilder` 플루언트 (스크립트/테스트만, 프로덕션 X) |
| **에러 표시 코드** | geny-executor의 `exec.*.*` 안정 식별자 1:1 매핑 | (변화 없음) | 자체 에러 enum 정의 (코드 중복) |
| **작업 큐** | ARQ (Redis) | + Temporal (긴 워크플로우) | Celery (무거움), BullMQ (Node), Kafka (과함) |
| **DB** | **PostgreSQL** (M0부터) | (HA/replica) | SQLite (동시 쓰기 한계 + 마이그레이션 비용), MongoDB (관계 부적합), DynamoDB (락인) |
| **캐시/큐/pubsub** | Redis | + NATS JetStream | Memcached (기능 약함) |
| **격리 런타임** | **Sysbox** runc | + gVisor one-shot, Kata (SaaS) | Docker 순수 (T6 위험), Firecracker 단독 (운영 복잡) |
| **컨테이너 오케스트레이션** | Docker Compose (단일 노드) | Kubernetes (옵션) | Swarm (모멘텀↓), Nomad (생태계↓) |
| **Git CLI/라이브러리** | `git` CLI subprocess | (변화 없음) | libgit2 (LFS X), go-git (worktree 누수), isomorphic-git (서버 약함) |
| **Git 호스트 어댑터 1차** | GitHub (`gh` CLI + REST) | + Forgejo, GitLab | (없음) |
| **CI/CD** | GitHub Actions 위임 + Compose Watch | + Woodpecker 임베드 | Tekton (K8s 전제), Drone (모멘텀↓), Jenkins X (복잡) |
| **배포 타깃 1차** | LocalCompose / RemoteSSH / Webhook | + Kubernetes / ArgoCD | (-) |
| **프록시/TLS** | Caddy + on-demand TLS | + Traefik (K8s) | nginx (TLS 설정 부담), HAProxy (over-spec) |
| **외부 공개** | (자체 IP) + cloudflared (opt-in) | + Tailscale Funnel | ngrok (freemium) |
| **프론트엔드 메타프레임워크** | **Vite + React (SPA)** | (변화 없음) | Next.js (RSC와 client-stateful 충돌), SvelteKit (생태계↓), Remix (가능 옵션) |
| **상태 관리** | Zustand + TanStack Query | (변화 없음) | Redux (오버), MobX (학습) |
| **UI 컴포넌트** | shadcn/ui + Tailwind | (변화 없음) | MUI / Mantine (종속), Chakra (모멘텀↓) |
| **에디터** | **Monaco + dockview** | + openvscode-server iframe 보조 | code-server 임베드 (LLM 채팅 강등), CodeMirror (LSP 약함), 자체 (낭비) |
| **터미널** | xterm.js + node-pty | (변화 없음) | (사실상 표준) |
| **인증 (IDP)** | MagicLink (자체) | + Authentik (OIDC), Zitadel | Keycloak (JVM 무거움), Auth0/Cognito (SaaS) |
| **정책 / 권한 평가** | **PolicyEngine** (config-driven, 기본 deny, owner 완화) | + ABAC (Casbin/Cedar) | hard-coded if/else 차단 ([[feedback_policy_config_not_hardcode]]) |
| **MFA** | TOTP | + WebAuthn (passkey) | SMS (보안 약함) |
| **시크릿** | OS keyring + SOPS+age | + Infisical, Vault | dotenv (평문), AWS SM (락인) |
| **메트릭** | Prometheus + cAdvisor + node_exporter | (변화 없음) | InfluxDB (모멘텀↓) |
| **로그** | (M0~) Postgres append-only (월 파티션); (M3+) Loki + Vector | — | ELK (Java 비용), SQLite (Postgres 통일) |
| **트레이싱** | OpenTelemetry (OTLP) → Tempo/Jaeger | + Honeycomb/Datadog (opt-in) | Zipkin (모멘텀↓) |
| **LLM 전용 관측** | (M3+) Langfuse (옵션) | + Helicone (SaaS opt-in) | LangSmith (LangChain 종속) |
| **영속 파일 스토리지** | **SeaweedFS** (Apache-2.0, 코어 동봉 — 옵션 아님) | (Filer/Volume Server 분리, 멀티노드) | host FS bind를 영속에 쓰지 말 것 — 캐시만, MinIO (AGPL 확대), S3 직접 (락인) |
| **이미지 레지스트리** | ghcr.io (외부) | + Distribution v2 (셀프호스트) | Harbor (헤비) |
| **MCP 서버 카탈로그** | 사용자 화이트리스트 | 큐레이션 카탈로그 | 자동 다운로드 (위험) |

---

## 10.2 핵심 결정 5개 — 상세 근거

### 10.2.1 백엔드: **Python + FastAPI**

| 후보 | + | − | 결과 |
|---|---|---|---|
| **Python + FastAPI** | Claude Code SDK 1급, MCP SDK 1급, geny-executor 동일 언어, 생산성, 비동기 asyncio | 인터프리터 오버헤드, 단일 프로세스 한계 | **★** |
| Node + Hono | TS SDK 1급, 프론트와 언어 통일 | MCP/Claude Code 통합 패턴이 Python보다 거침, asyncio 표현력 부족 | — |
| Go + gin/echo | 단일 바이너리, 강한 동시성 | Claude Code SDK 없음(CLI 호출만), 도구 콜백 코드 거침 | — |
| Rust + axum | 단일 바이너리, 안전 | SDK 부족, 개발 속도 손해 | — |

결정 근거:
- [04](04_llm_agent_layer.md): geny-executor 직접 의존 → 같은 언어 필수.
- MCP SDK + Anthropic SDK가 Python에서 가장 자연스러움.
- 컨테이너 데몬도 Python으로 → 단일 언어 운영.

### 10.2.2 에이전트 엔진: **geny-executor 2.1.0+ manifest-driven**

핵심 원칙 ([[feedback_extend_executor_not_adapter_layer]] + [[reference_geny_executor_v2_1]]):
- GAPT 에이전트 레이어 = `EnvironmentManifest` JSON 템플릿 + `Pipeline.from_manifest_async(...)` 호출. *그게 전부*.
- 1차 provider = `claude_code_cli` (Stage 6 `config.provider`). 호스트 도구는 **MCP wrap**(`extras["mcp_config"]`)으로 CLI에 노출. SDK provider도 manifest로 선택 가능 (executor가 5개 내장).
- 신규 모델/도구/strategy는 executor 본체에 PR → GAPT는 의존 버전만 올림.
- GAPT 측에 `LlmAgentBackend` 같은 Protocol을 두지 않는다 — *두 번 추상화*하는 셈. ProjectAwareSessionManager가 직접 호출.

| 후보 | + | − | 결과 |
|---|---|---|---|
| **geny-executor manifest-driven 직접 의존** | 21단계 검증, claude_code_cli 1급, manifest 단일 진실원, Geny와 능력 공유, `exec.*.*` 에러 코드 안정 | upstream PR 사이클 필요 | **★ 영구 원칙** |
| `PipelineBuilder` 플루언트 직접 사용 (프로덕션) | 코드로 빌드 친숙 | manifest 단일 진실원 깨짐, A/B 비교 불가 | ❌ (스크립트/테스트만) |
| GAPT에 `LlmAgentBackend` Protocol + 다중 구현체 | 추후 교체 자유도 | 이중 추상화, 코드 부풀림 | ❌ |
| Fork | 빠른 변경 | 평행 진화, Geny 분기 | ❌ |
| 자체 재작성 | 클린 슬레이트 | 재발명 | ❌ |

### 10.2.3 격리: **Sysbox**

| 후보 | + | − | 결과 |
|---|---|---|---|
| **Sysbox runc** | 안전한 DinD 1급, 사용자 compose 그대로 동작, Apache 2.0, 운영 단순 | Linux 한정, 호스트 sysbox-runc 설치 | **★** |
| 순수 Docker (runc) | 가장 단순 | T6(호스트 RCE) 위험 | ❌ |
| gVisor | 유저스페이스 커널, 강한 격리 | DinD X, 호환 제약 | One-shot 보조 |
| Kata Containers | VM 격리 | 메모리↑, 운영 복잡 | M4+ SaaS |
| Firecracker | 빠른 microVM | OCI 어댑터 필요, 인프라 복잡 | E2B 스타일 시 |

근거: [06](06_isolation_and_runtime.md) 위협 T6(호스트 docker.sock 노출 = RCE)를 막으면서 사용자 *기존 compose 패턴*을 깨지 않는 유일한 답.

### 10.2.4 프론트엔드: **Vite + React + Monaco + dockview**

| 후보 | + | − | 결과 |
|---|---|---|---|
| **Vite + React SPA** | IDE같은 stateful client에 깔끔, 백엔드 분리 자연 | SSR 없음 | **★** |
| Next.js App Router | SSR/RSC | client-stateful과 RSC 충돌, 빌드 복잡 | ❌ |
| **Monaco + dockview** | LLM 채팅 1급 가능, 라이선스 무관, 가벼움 | 직접 구현 부담 | **★** |
| code-server iframe | 풀 VS Code | LLM 채팅 강등, MS EULA, 메모리 | 보조 (M3+) |
| openvscode-server | Open VSX 친화 | 동일 강등 | 보조 (M3+) |

근거: [08](08_web_ide_ux.md) 핵심 가치 V2(AI 1급 시민)를 풀 IDE 임베드로는 달성 불가.

### 10.2.5 CI/CD 1차: **GitHub Actions 위임 + Compose Watch**

| 후보 | + | − | 결과 |
|---|---|---|---|
| **GH Actions 위임** | 사용자 이미 씀, 셋업 0 | GH 종속, 분 한도 | **★ outer loop** |
| **Compose Watch** | 사용자 dev 친숙 | 사용자 compose에 watch 필요 | **★ inner loop** |
| Woodpecker 임베드 | 셀프호스트, 가벼움 | 셋업 비용, 새 학습 | M3+ 옵션 |
| Tekton/Argo | K8s 1급 | 코어에 K8s 강제 안 함 | M4+ K8s 단계만 |

근거: [07](07_cicd_and_preview.md) — 사용자가 이미 쓰는 도구를 *그대로 받음*이 Coolify 정신.

---

## 10.3 라이선스 / 비용 함정 체크리스트

각 결정의 *법적*·*비용* 함정. 출시 전 검토 필수.

| # | 함정 | 영향 | 우리의 대응 |
|---|---|---|---|
| L1 | **VS Code Marketplace EULA** — "Visual Studio 패밀리 외 사용 금지" | code-server/openvscode가 MS Marketplace로 우회 설정 시 EULA 위반 | 보조 IDE 모드에서도 **Open VSX 한정**, 우회 설정 가이드 *동봉 금지* |
| L2 | **Docker Desktop 상용 라이선스** | 250+ 직원 또는 매출 $10M+ 회사의 상업 사용 유료 | 호스트는 *Linux + Docker Engine 직접 설치*. Desktop은 지원하지 않음. |
| L3 | **MinIO 라이선스 변경 (AGPL 적용 확대)** | 배포물에 번들 시 사용자 의무 발생 | **MinIO 사용하지 않음.** 대체로 **SeaweedFS** (Apache-2.0, S3-호환) 채택. compose 옵션에 image 참조만 |
| L4 | **Daytona OSS = AGPL-3.0** | 코드를 fork해서 배포물에 섞으면 의무 큼 | 참고 자료로만, 코드 차용 ❌ |
| L5 | **Forgejo = GPLv3+** | 자체 사용 무관, 배포물에 묶으면 GPL 전파 가능 | M3+ *옵셔널 외부 서비스*, compose에 image 참조만 |
| L6 | **Claude Code SDK = 사유 + 토큰 과금** | 사용자별 BYO 키 미설계 시 운영자 결제 폭증 | M0부터 BYO 키 1급, 우리 카드 결제 없음 |
| L7 | **MCP 서버 카탈로그 — 일부 라이선스/품질 미검증** | 자동 spawn 시 사용자 위험 | 화이트리스트만, 카탈로그 큐레이션 |
| L8 | **Sentry self-host 라이선스 변경** (BSL) | 셀프호스트 코어는 가능하나 SaaS 재판매 금지 | 우리는 직접 통합 안 함, OTLP 라우팅만 |
| L9 | **Langfuse 라이선스 (MIT + 상용 클라우드 제한)** | 셀프호스트 무료, 우리가 *SaaS 재판매* 시 제한 | M3+ 옵션, 사용자 자체 셀프호스트 권장 |
| L10 | **GitHub Actions 무료 분 한도** | 사용자 인지 부족 시 청구 폭증 | UI에서 일별 사용량 표시, 한도 임박 경고 |
| L11 | **Anthropic 비용 (특히 Opus + Extended Thinking)** | 한 사용자가 하루 $100+ 가능 | [09](09_security_authz_observability.md) cost cap 게이트, 명확한 경고 |
| L12 | **cloudflared 무료 — 폭주 시 약관 검토 필요** | 일반 사용엔 무관 | 사용 한도 가이드, opt-in |
| L13 | **Tailscale Funnel 무료 한도** | 노출량/대역폭 한계 | 가이드, opt-in |
| L14 | **GitHub OAuth App publish rate limit** | 다수 사용자가 같은 OAuth App 사용 시 한계 | 사용자별 자체 OAuth App 등록 가이드 (M3+) |
| L15 | **GenAI 모델 비용 변동** | 모델 가격 외부 설정으로 미관리 시 계산 어긋남 | `models.yaml` 외부 파일, 정기 갱신 |

[[feedback_solo_hobby_licensing]] 정신에 따라 — *솔로 호비스트* 자체 사용에선 대부분 무관. 그러나 *배포물 형태로 공개*할 때 위 함정이 *사용자에게* 적용될 수 있으므로 *정보성 경고*를 README/docs에 명시.

---

## 10.4 컨테이너 이미지 의존성 표

`gapt/runtime:latest` 베이스에 포함되는 모든 패키지 목록 (라이선스 포함):

| 패키지 | 라이선스 | 비고 |
|---|---|---|
| debian:bookworm-slim | base — 자유 사용 | |
| git, git-lfs | GPLv2 | 동적 링크 — 배포 영향 작음 |
| gh CLI | MIT | |
| docker-ce CLI + compose plugin | Apache 2.0 | |
| python3.12 | PSF | |
| uv | Apache 2.0 / MIT dual | |
| node 22 LTS | MIT | |
| npm, pnpm, yarn | Artistic / MIT / BSD | |
| gcc, make, build-essential | GPLv3 (도구), GPLv2+ | |
| curl, ca-certificates | MIT / MPL | |
| toolkit-agent (GAPT) | Apache 2.0 (예정) | |
| claude (옵셔널, 사용자가 설치 트리거) | Anthropic 사유 | |

모든 패키지의 SBOM(Software Bill of Materials)을 *CI에서 자동 생성* (syft 등) — M3+ 공급망 보안 강화 시.

---

## 10.5 외부 SaaS의존성 / 옵셔널 통합

| 서비스 | 우리에게의 역할 | 라이선스/비용 | 기본 |
|---|---|---|---|
| Anthropic API | LLM 1차 | 사용자 BYO 키 | (사용자 등록 필요) |
| OpenAI API | (Aider 어댑터) | BYO | opt-in |
| GitHub API | Git 호스트 1차 | 사용자 OAuth | (등록 후) |
| GitLab API | Git 호스트 옵션 | BYO | opt-in |
| Cloudflare Tunnel | 외부 공개 | 무료 | opt-in |
| Slack/Discord webhook | 알림 | 사용자 webhook | opt-in |
| Sentry / Datadog / Honeycomb | 텔레메트리 | 사용자 BYO | opt-in |
| AWS S3 / Backblaze B2 | 백업 | 사용자 자체 | opt-in |

원칙: **외부 SaaS는 기본 OFF.** 사용자가 명시적으로 켜면 *명확히 어떤 데이터가 나가는지* 표시.

---

## 10.6 안 쓰는 기술 — 그리고 안 쓰는 이유

| 기술 | 안 쓰는 이유 |
|---|---|
| LangChain / LangGraph | geny-executor 정신: 프레임워크가 너무 많이 숨김. 직접 Anthropic SDK |
| Helm (코어) | M4+ K8s 단계에서만 부분 도입 |
| Terraform (코어) | 단일 노드 Compose 모드에는 과함. K8s 단계 옵션 |
| MinIO | AGPL 확대로 인한 사용자 의무 부담. **SeaweedFS (Apache-2.0) 코어 동봉으로 대체** — 영속 파일은 무조건 SeaweedFS |
| host FS bind (영속용) | 영속 파일은 무조건 SeaweedFS. host FS는 *캐시/임시* 한정 |
| SQLite | 동시 쓰기/단일 writer 한계, 향후 마이그레이션 비용. 처음부터 PostgreSQL |
| 별도 LLM 어댑터 클래스 (Aider/OpenHands/Cursor CLI 등) | geny-executor에 provider로 일반화가 원칙. 상위 앱 다층화 금지 ([[feedback_extend_executor_not_adapter_layer]]) |
| GAPT 측 `LlmAgentBackend` Protocol | geny-executor의 `Pipeline.from_manifest_async`가 이미 추상화의 정점. 이중 추상화 회피 ([04](04_llm_agent_layer.md) §4.11) |
| `PipelineBuilder` 플루언트 (프로덕션) | manifest 단일 진실원 원칙 위반 — 스크립트/테스트만 사용 ([[reference_geny_executor_v2_1]]) |
| GraphQL | 단순 REST + SSE가 충분. Apollo 클라이언트 의존 부담 |
| gRPC (외부) | 내부 데몬↔컨트롤은 unix socket HTTP로 충분. 외부 API는 REST |
| Kafka | 작업 큐 단계엔 과함. Redis Streams면 충분 |
| Elasticsearch | 검색은 컨테이너 안 `rg`/`fd` + Postgres FTS / `pg_trgm`. ES는 운영 부담 큼 |
| MongoDB | 관계 데이터 부적합 |
| Auth0 / Clerk | SaaS 락인. 셀프호스트 IDP 우선 |
| Datadog / New Relic (코어) | 비용 + 락인. OTLP로 사용자 자체 라우팅 |
| Vault Enterprise | OSS 코어로 충분. 엔터프라이즈는 별도 라인 |

---

## 10.7 다국어 / 폰트 / 디자인 시스템

- 영어 + 한국어 (P1). i18next.
- 폰트: Inter (UI), Geist Mono / JetBrains Mono (코드).
- 컬러 토큰: CSS variables, 다크모드 1차.
- 디자인 시스템: shadcn/ui base + GAPT 토큰 layer.
- 아이콘: Lucide React (MIT).

---

## 10.8 빌드 / 배포 (toolkit 자체)

- 백엔드: `uv build` → wheel + Docker image (`ghcr.io/gapt/server`).
- 프론트: `vite build` → 정적 자산, 백엔드 image에 포함 (Caddy가 서빙).
- 데몬: `uv build` → wheel, 컨테이너 이미지에 포함 (`gapt/runtime`).
- 단일 docker-compose.yml로 부팅 (사용자 셋업).
- 우리 자체 CI: GitHub Actions. 우리 배포: 우리 GAPT 자기 호스트 (eat-our-dogfood).

---

## 10.9 *지금 결정하기 어려운* 사항

[[feedback_durable_instructions]] 정신에 따라 결정 보류 항목을 *명시*:

1. **언어 1언어 강제 vs 다언어 허용**: Python을 1차로 정했지만 *데몬도 Python*인지 *데몬은 Go*인지는 운영 부담 측면에서 더 봐야. 1차 결정: 데몬도 Python (단일 언어 운영).
2. **MS LSP(pyright/tsserver) 라이선스 영역**: 별도 검토 필요.
3. **자체 MCP 서버 카탈로그 운영**: 보안 책임이 무거움. 큐레이션 방식 미결.
4. **K8s 백엔드 도입 시점**: P3 페르소나 수요 신호 보고 결정.
5. **유료 라인** (M5+ 가능성): 코어 OSS + 추가 기능 (관리 콘솔, 클라우드 백업, SLA 지원 등). 비즈니스 모델 미결.

---

## 10.10 결정 적용 추적

각 결정은 *어느 문서에서 정의되었는지*를 표시. 후속 변경 시 추적용.

| 결정 | 정의처 |
|---|---|
| 백엔드 = Python+FastAPI | [04](04_llm_agent_layer.md), 본 문서 |
| 에이전트 엔진 = geny-executor | [04](04_llm_agent_layer.md) |
| 격리 = Sysbox | [06](06_isolation_and_runtime.md) |
| 프론트 = Vite/Monaco/dockview | [08](08_web_ide_ux.md) |
| CI/CD = GH Actions + Compose Watch | [07](07_cicd_and_preview.md) |
| Git CLI subprocess | [05](05_git_workflow.md) |
| IDP 어댑터 = MagicLink → Authentik | [09](09_security_authz_observability.md) |
| Secret = OS keyring + SOPS → Infisical | [09](09_security_authz_observability.md) |
| 감사 = ULID JSON | [09](09_security_authz_observability.md) |
| 관측 = Prometheus + OTel | [09](09_security_authz_observability.md) |
| 프록시 = Caddy on-demand TLS | [07](07_cicd_and_preview.md) |
| 외부 공개 = cloudflared (opt-in) | [07](07_cicd_and_preview.md) |
| 모든 결정이 어댑터 인터페이스 뒤에 | [03](03_system_architecture.md) |

다음 [11](11_roadmap.md)에서 이 결정들을 *단계별 마일스톤*에 배치한다.
