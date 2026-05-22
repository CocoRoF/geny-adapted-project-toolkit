# 00. 프로젝트 개요 (Overview)

> **문서 패밀리**: `geny-adapted-project-toolkit / docs / `
> **단계**: Phase 0 — docs-first
> **작성일**: 2026-05-22
> **다음 문서**: [01_market_landscape.md](01_market_landscape.md)

---

## 0.1 한 줄 정의

> **내 서버에 띄우는 `OpenHands × Coolify` 합본 + `Cursor`-급 라이브 편집 UI.**
> 외부 Git 레포(예: Geny)를 `git clone` → **Docker(Sysbox) 격리 컨테이너** → **Claude Code 기반 LLM**으로 편집·테스트·빌드·배포까지 **하나의 웹 콘솔**에서 끝낸다.

이름의 어원은 단지 **브랜드 일관성**(geny-*) 일 뿐, Geny 본체와 직접적인 코드 의존은 갖지 않는다. Geny는 본 toolkit의 *첫 어댑트 사례*일 뿐이다.

---

## 0.2 왜 지금 이걸 만드는가

### 시장의 빈자리 (자세한 분석은 [01](01_market_landscape.md))

2026년 5월 현재, 다음 5가지를 *동시에* 제공하는 단일 제품은 사실상 존재하지 않는다.

| 요건 | 가장 가까운 후보 | 우리와의 갭 |
|---|---|---|
| 셀프호스트 (자기 서버에 띄움) | Coolify, OpenHands, Coder | OpenHands는 *코드 에이전트*만, Coolify는 *배포*만 |
| AI 코드 에이전트 (Claude Code급) | Cursor, Windsurf, OpenHands | Cursor는 SaaS-only, OpenHands는 IDE-like UX 약함 |
| 멀티 프로젝트 (여러 외부 레포 동시) | GitLab Duo, Coder, Cody Enterprise | GitLab 안에서만, 또는 엔터프라이즈 가격대 |
| 내장 CI/CD + 배포 | GitHub Actions, Coolify, Vercel | 코드 에이전트와 분리됨 |
| IDE-like 라이브 편집 UI | Cursor, Zed, v0.app | 셀프호스트 불가 또는 벤더 클라우드 종속 |

**근접한 단 하나의 후보 GitLab Duo Agent Platform**도 *GitLab 안에서만* 성립한다. 외부 GitHub/Gitea 레포가 일급 시민이 되어야 하는 시나리오를 만족시키지 못한다.

### 윈도우는 6~12개월

OpenHands와 Coder Agents는 이쪽으로 *수렴* 중이다. 즉 비어 있는 자리는 *닫히는 중인 빈자리*다. Claude Code SDK + MCP + ACP 같은 표준이 2025–2026 사이에야 자리잡으면서 이런 통합 플랫폼을 만들 *재료*가 비로소 충분해졌다.

### 사용자(개발자/운영자) 관점의 통점 (pain point)

- **여러 사이드 프로젝트가 흩어져 있다** — 각자 다른 VPS, 다른 docker compose, 다른 CI 설정. 매번 SSH 들어가서 `cd` 하고 `git pull` 하고 `docker compose up -d`.
- **AI 에디터는 로컬 머신에만 산다** — Cursor를 데스크탑에서 띄워야 한다. 출장 중 폰으로 한 줄 고치고 싶어도 불가능.
- **AI 에디팅과 배포가 단절** — Cursor에서 코드 고치고 → 푸시 → CI 결과 다른 탭에서 확인 → 서버에 SSH 들어가서 `docker compose pull && up -d`. 그리고 어딘가에서 깨진다.
- **세션 격리가 약하다** — 같은 머신에서 두 프로젝트 동시 LLM 작업을 돌리면 도구 권한·환경변수·디렉토리가 섞인다.
- **셀프호스트 옵션이 없다** — 코드와 비밀이 SaaS 벤더에게 흘러나가는 게 싫다.

---

## 0.3 핵심 가치 명제 (Value Proposition)

다섯 가지 가치 축으로 표현한다.

### V1. *One Console for All My Projects*
하나의 웹 콘솔(`https://toolkit.my-server.com`)에 로그인하면 내가 가진 모든 외부 레포가 좌측 트리에 나열된다. 클릭 한 번으로 그 프로젝트의 IDE 워크스페이스에 진입.

### V2. *AI Agent as First-Class Citizen*
LLM(Claude Code SDK 우선, Aider/Cline는 어댑터)이 *사이드 패널 확장*이 아닌 **워크스페이스의 일급 시민**. 채팅 → diff 리뷰 → 적용 → 테스트 → 배포의 모든 단계에서 같은 세션이 살아 있다.

### V3. *Isolation by Default*
모든 프로젝트는 별도의 Sysbox 컨테이너에서 실행된다. LLM이 만든 코드가 호스트의 다른 프로젝트나 호스트 자체에 영향을 주지 않는다. 솔로 사용에서도, 멀티 사용자 SaaS로 진화할 때도 *같은 격리 모델*을 쓴다.

### V4. *Closed Loop: Edit → Build → Deploy*
편집한 코드가 같은 화면에서 빌드되고, 같은 화면에서 사용자 인프라(로컬 도커, 원격 VPS, K8s)로 배포된다. CI 결과·런타임 로그·메트릭이 같은 콘솔에서 보인다.

### V5. *Self-Hostable, BYO Everything*
바이너리 하나 + 1행 `docker compose up`으로 띄운다. LLM 키도 사용자, Git 인증도 사용자, 인프라도 사용자, 데이터도 사용자. **벤더 락인 0.** Phase 0 단계의 코어는 Apache-2.0 (또는 MIT) 지향.

---

## 0.4 비-목표 (Non-Goals)

**중요한 결정은 *무엇을 안 할지*에 더 많이 들어 있다.** Phase 0~M3 동안 이 toolkit은 다음을 *명시적으로* 하지 않는다.

| 비-목표 | 이유 |
|---|---|
| **신규 앱 *생성* (Lovable/v0/Bolt 류)** | 우리 가치는 *기존* 외부 레포에 있다. "프롬프트→풀스택 앱"은 다른 게임. 향후 확장 검토만. |
| **로컬 IDE 확장 (Cline/Continue 류)** | 우리는 *서버에 떠 있는 웹 콘솔*. 데스크탑 머신 위 확장은 별도 카테고리. |
| **자체 모델 학습/파인튜닝** | LLM은 외부 SDK로 위임. 모델 라우팅/계측만 1급. |
| **K8s/멀티 노드 클러스터 (M3까지)** | 단일 노드 Docker Compose + Sysbox로 시작. K8s는 M4 이후 옵셔널 백엔드. |
| **사내 Git 호스팅 (Gitea/Forgejo) 임베드 (M3까지)** | 외부 GitHub/GitLab API만으로 시작. 사용자가 이미 가진 Git에 접속. M3+에서 Forgejo 옵션. |
| **자체 모델 카탈로그/마켓플레이스** | Anthropic SDK 1차, 어댑터로 Aider/OpenRouter 추가. 모델 큐레이션 비즈니스는 안 함. |
| **데스크탑 앱** | 웹만. PWA로 모바일/태블릿 보조. |
| **블록체인/Web3/AI 에이전트 마켓플레이스** | 우리는 *DevOps 툴*이다. |

이 비-목표는 [11_roadmap.md](11_roadmap.md)에서 단계별로 *언제 어떤 비-목표가 풀릴 수 있는지* 다시 다룬다.

---

## 0.5 사용자/페르소나 요약 (자세히는 [02](02_use_cases_and_personas.md))

| 페르소나 | 1단계 메인 | 2단계 확장 |
|---|---|---|
| **솔로 호비스트 (P1)** | ✅ 1차 사용자. VPS 하나에 여러 사이드 프로젝트. | — |
| **소규모 팀 리드 (P2)** | ✅ 2~10명, 멀티 사용자/RBAC 필요 | — |
| **사내 플랫폼 엔지니어 (P3)** | ⚠️ 인터페이스 호환되도록 설계 | M4+에서 일급 지원 |
| **에이전트 자동 운영자 (P4: 미래)** | — | M5+ |

> *MEMORY에 따른 결정*: 현 사용자는 솔로 호비스트 포지션이지만 **엔터프라이즈 인터페이스를 미리 고려한 설계 필수**. P3 페르소나는 *지금 만족시키지 않지만, 지금의 설계가 그들을 막지 않도록* 한다.

---

## 0.6 핵심 시나리오 한 컷 (Golden Path)

```
[09:00] 사용자가 https://toolkit.my-server.com 에 접속, magic-link 로그인.
[09:00] 좌측 트리에 등록된 4개 프로젝트가 보임 (Geny / geny-avatar / blog / experiment-x).
[09:01] "Geny" 클릭 → 워크스페이스 진입. 백엔드가 Sysbox 컨테이너를 부팅하고
        그 안에서 git pull origin main, docker compose -f compose.dev.yml up -d.
[09:02] 우측 채팅 패널에 "executor 통합의 다음 단계로 v0.21.0 cycle 시작" 입력.
        gapt_default.json manifest 기반 Pipeline 인스턴스가 살아남.
        claude_code_cli provider가 spawn한 claude CLI가 host MCP wrap을 통해
        GAPT의 gapt_git/gapt_compose 도구를 native MCP로 호출.
        plan/progress/analysis 폴더는 Stage 3 시스템 prompt에 자동 주입 (Geny의 기존 cadence).
[09:05] 에이전트가 5개 파일 수정 diff를 제안. 우측 dockview 패널에 사이드-바이-사이드 diff 표시.
        사용자가 한 파일은 거부, 네 개는 승인.
[09:06] 워크트리에 자동 커밋. inner loop 워처가 dev 컨테이너 재기동.
        프리뷰 iframe이 https://geny.preview.my-server.com 으로 뜸.
[09:10] 사용자: "테스트 추가하고 PR 올려줘"
[09:14] 새 브랜치, 테스트 작성, push, gh CLI로 PR 생성. GitHub Actions 결과가 같은 패널에 스트림.
[09:25] CI 그린. "prod에 배포" 클릭. 사전 정의된 deploy 파이프라인(웹훅 또는 Woodpecker)이
        prod 컨테이너의 docker compose pull && up -d 실행.
[09:27] 배포 완료. Grafana 패널이 latency/에러율 OK 보고.
```

이 시나리오의 모든 단계가 **하나의 브라우저 탭, 하나의 LLM 세션, 하나의 컨텍스트**에서 일어난다는 점이 본질이다.

---

## 0.7 시스템 한 페이지 요약 (자세히는 [03](03_system_architecture.md))

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser (Vite + React + Monaco + dockview + xterm.js)               │
│  ─ Project Tree ─ Editor ─ Chat ─ Terminal ─ Preview iframe ─ Logs   │
└──────────────────────────────────────────────────────────────────────┘
                        ▲                    ▲
                   SSE/WebSocket         REST/RPC
                        │                    │
┌──────────────────────────────────────────────────────────────────────┐
│  Toolkit Backend (Python + FastAPI + ARQ on asyncio)                 │
│  ┌────────────┐ ┌──────────────┐ ┌───────────────┐ ┌──────────────┐ │
│  │ Project    │ │ Agent        │ │ Build / Deploy│ │ Audit /      │ │
│  │ Service    │ │ Session Mgr  │ │ Pipeline      │ │ Observability│ │
│  │ (multi-prj)│ │ (geny-exec)  │ │ Orchestrator  │ │              │ │
│  └────────────┘ └──────────────┘ └───────────────┘ └──────────────┘ │
│  ┌────────────┐ ┌──────────────┐ ┌───────────────┐ ┌──────────────┐ │
│  │ Git Service│ │ Sandbox Ctrl │ │ Secret Vault  │ │ Auth (IDP)   │ │
│  │ (gh/git)   │ │ (Sysbox)     │ │ (keyring/SOPS)│ │              │ │
│  └────────────┘ └──────────────┘ └───────────────┘ └──────────────┘ │
│           PostgreSQL  +  Redis  +  **SeaweedFS** (영속 파일 코어)    │
└──────────────────────────────────────────────────────────────────────┘
        │                    │                         │
        ▼                    ▼                         ▼
┌──────────────┐   ┌──────────────────┐   ┌──────────────────────────┐
│ Per-Project  │   │ Per-Project      │   │ External Services        │
│ Sysbox Box A │   │ Sysbox Box B     │   │  - GitHub / Gitea / GH   │
│ (Geny)       │   │ (geny-avatar)    │   │  - Anthropic API         │
│  ─ git clone │   │  ─ git clone     │   │  - Container Registry    │
│  ─ Compose   │   │  ─ Compose       │   │  - cloudflared (옵션)    │
│  ─ Claude CC │   │  ─ Claude CC     │   │                          │
└──────────────┘   └──────────────────┘   └──────────────────────────┘
```

핵심 요점:
- **컨트롤 플레인**(Toolkit Backend)은 호스트에 1개.
- **데이터/실행 플레인**(per-project Sysbox 컨테이너)은 *프로젝트 수 × 활성 사용자 수* 만큼 동적으로 생성.
- 컨트롤 플레인에서 호스트 docker 소켓을 직접 노출하지 않는다. 격리 컨테이너 안에 진짜 dockerd가 돈다(Sysbox의 안전한 DinD).

---

## 0.8 핵심 원칙 (Design Tenets)

다음 7개 원칙은 모든 후속 결정의 *심판 기준*이다.

1. **Isolation by Default**
   *어떤 컴포넌트도 격리를 풀어주는 게 *옵션*이 아니라 *결정 사항*이다.* 호스트 docker 소켓 마운트는 어떤 모드에서도 기본값이 아님.

2. **External Repo is First-Class**
   사용자가 이미 가진 GitHub/Gitea 레포가 일급 시민. 내장 Git 호스팅은 부가물(M3+ 옵션).

3. **No Vendor Lock-in**
   LLM, Git 호스트, 컨테이너 레지스트리, 시크릿 저장소, IDP — 모두 *교체 가능한 어댑터*. Anthropic SDK 1차 통합이지만 Aider/OpenRouter 어댑터 가능 구조.

4. **Self-Host or Get Out**
   기능을 SaaS-only로 만들지 않는다. 클라우드 부가 서비스(예: 호스팅, 컴퓨트 크레딧)는 *순수 옵션*이며 코어는 단일 노드에서 100% 동작.

5. **Reuse Battle-Tested Components**
   직접 만들지 않는다. Sysbox·Caddy·Monaco·dockview·xterm·geny-executor — 검증된 부품을 조립한다.

6. **Live Edit > Batch Generate**
   Cursor 식 *작은 변경의 즉시 반영*이 Bolt/v0 식 *큰 생성*보다 본 시나리오에 맞다. 토큰 비용과 신뢰성 모두 더 낫다.

7. **Audit Everything**
   LLM의 모든 통화·도구 호출·파일 편집·배포 명령은 감사 로그에 기록 (자세히는 [09](09_security_authz_observability.md)). 신뢰의 게이트는 *투명성*.

8. **Policy by Default, Not by Code**
   위험한 액션(LLM 직접 prod 배포 등)을 *코드에 박힌 절대 금지*로 두지 않는다. **PolicyEngine의 기본값**으로 strict하게 두고, 사용자가 책임지고 config로 완화/강화 가능. 솔로 자동화부터 엔터프라이즈 strict까지 같은 메커니즘 ([09](09_security_authz_observability.md) §9.2.3).

9. **Single Agent Backend, Manifest-driven**
   에이전트 능력은 *상위 앱 어댑터를 다층화하지 않고* geny-executor 2.1.0+ 한곳에 모은다. **1차 backend = `claude_code_cli` provider + host MCP wrap**으로 GAPT 도구를 CLI에 노출. **모든 환경 = `EnvironmentManifest` JSON 단일 진실원**. 신규 모델/도구/strategy는 executor에 PR → GAPT는 의존 버전과 manifest만 올림 ([04](04_llm_agent_layer.md) §4.1, [[reference_geny_executor_v2_1]]).

---

## 0.9 가장 큰 리스크 5개 (자세히는 [09](09_security_authz_observability.md)/[11](11_roadmap.md))

| 리스크 | 본질 | 1차 대응 |
|---|---|---|
| **R1. 격리 실패** | Sysbox 우회·DinD 탈출·도커 소켓 누수 → 호스트 RCE | Sysbox 기본, 호스트 소켓 일체 비-노출, M1에서 격리 페네트레이션 테스트 |
| **R2. LLM 무단 도구 사용** | `allowedTools` 화이트리스트 우회, hooks 우회, 사용자가 정책 과도 완화 | Claude Code SDK `permissionMode` + 자체 `PolicyEngineGuard` (Stage 4 확장) — 정책 완화는 owner-only + 추가 확인 + audit |
| **R3. 토큰/비용 폭주** | 무한 루프 또는 사용자가 잠든 사이 토큰 소모 | Stage 4 Guard `cost_budget_usd` + 프로젝트별/세션별 한도 + UI 경고 |
| **R4. 시크릿 누출** | LLM 응답에 API 키 포함, 컨테이너 환경 변수 노출 | 시크릿 저장소(OS keyring/SOPS) + 컨테이너 주입 시 단명, 로그 정규식 마스킹 |
| **R5. 라이선스 함정** | MS Marketplace EULA, Docker Desktop 상업 라이선스, MinIO AGPL 등 | [10](10_tech_stack_decisions.md)에 라이선스 결정 매트릭스 명시, [[feedback_solo_hobby_licensing]] 따름 |

---

## 0.10 성공의 정의 (Definition of Done — Phase 0~M1)

본 toolkit이 "최소한 자기 자신을 정당화한다"고 말할 수 있는 기준:

- [ ] **자기 자신을 호스팅(self-bootstrap)**: 본 toolkit이 본 toolkit의 레포를 self-host로 운영하며, 내 코드를 내 toolkit에서 편집·배포할 수 있다.
- [ ] **Geny 첫 어댑트 성공**: Geny 레포를 toolkit에 등록해서, agent_session_manager 영역의 한 사이클(plan→implement→test→PR→deploy)을 *외부 IDE 없이* 완수.
- [ ] **격리 검증**: 컨테이너 내부에서 `docker run --privileged -v /:/host` 류 공격이 호스트에 영향을 주지 못함을 외부자(또는 사용자) 검증.
- [ ] **비용 가시성**: 어제 LLM 사용량(요청/토큰/USD)을 30초 안에 답할 수 있다.

---

## 0.11 용어집 (Glossary)

- **Toolkit / GAPT**: 본 프로젝트(`geny-adapted-project-toolkit`)의 약칭.
- **Project**: toolkit에 등록된 외부 Git 레포. 1프로젝트 ↔ 1격리 컨테이너 ↔ N세션.
- **Workspace**: 한 프로젝트의 사용자별 라이브 작업 공간(파일 트리·에디터 상태·세션).
- **Sysbox**: 컨테이너 격리 강화 런타임. 컨테이너 안에 실제 dockerd를 안전하게 실행. → [06](06_isolation_and_runtime.md)
- **Worktree**: git의 `git worktree` 메커니즘. 같은 .git 디렉토리 + 여러 브랜치를 동시 체크아웃. → [05](05_git_workflow.md)
- **Agent Session**: 한 프로젝트 컨텍스트에서 살아 있는 LLM 대화 단위. geny-executor의 Session을 확장 사용. → [04](04_llm_agent_layer.md)
- **Inner Loop / Outer Loop**: Inner = 컨테이너 내부 *변경→재기동*(=`compose watch`), Outer = git push → CI/CD. → [07](07_cicd_and_preview.md)
- **PolicyEngine**: 위험 액션(LLM 직접 prod 배포 등)의 *허용/거부/추가확인*을 결정하는 config-driven 게이트. 기본 strict + owner가 완화 가능. → [09](09_security_authz_observability.md) §9.2.3
- **Single Agent Backend**: GAPT의 모든 LLM 호출은 `geny-executor 2.1.0+`의 `Pipeline.from_manifest_async`를 통과. 별도 어댑터/Protocol 다층화 X. → [04](04_llm_agent_layer.md) §4.1
- **Manifest**: `EnvironmentManifest` JSON. GAPT 환경의 단일 진실원. 1차 템플릿 = `gapt_default.json`. → [04](04_llm_agent_layer.md) §4.3
- **CLI provider / SDK provider**: 5개 provider 중 `claude_code_cli`(1차) + 4 SDK(anthropic/openai/google/vllm). manifest `stages[6].config.provider`에서 선택. → [04](04_llm_agent_layer.md) §4.5
- **Host-attached MCP vs CLI MCP wrap**: 둘 다 사용. 전자는 모든 provider 공통 도구 노출, 후자는 `claude_code_cli` 한정으로 GAPT 자신의 도구 노출. → [04](04_llm_agent_layer.md) §4.5
- **HookRunner**: geny-executor의 PRE/POST tool-use 훅. GAPT의 PolicyEngine이 그 위에 구현. → [04](04_llm_agent_layer.md) §4.6
- **`exec.*.*` 에러 코드**: geny-executor의 안정 식별자. GAPT는 자체 정의 없이 그대로 사용. → [04](04_llm_agent_layer.md) §4.10
- **MCP**: Model Context Protocol. Claude Code의 도구/리소스 접속 표준.
- **ACP**: Agent Client Protocol. Zed가 주도하는 에이전트-에디터 인터페이스 표준.
- **BYO**: Bring-Your-Own. 사용자가 자기 LLM 키/Git 인증/인프라를 가져옴.
- **GAPT의 "어댑트(adapt)"**: 외부 프로젝트를 toolkit에 *맞추는* 것 — 격리·자동화·메타데이터 등록.

---

## 0.12 후속 문서 가이드

| 번호 | 제목 | 한 줄 |
|---|---|---|
| 01 | 시장/경쟁 풍경 | 우리의 빈자리 정의, 12+ 경쟁 제품 비교, 포지셔닝 맵 |
| 02 | 유스케이스/페르소나 | P1~P4 페르소나, 5개 골든패스 시나리오, 비-시나리오 |
| 03 | 시스템 아키텍처 | 컨트롤/데이터 플레인 분리, 도메인 경계, 데이터 흐름도 |
| 04 | LLM 에이전트 레이어 | geny-executor 재사용 전략, 멀티 세션, Claude Code SDK 통합 |
| 05 | Git 워크플로 | clone/worktree/PR/auth, 다중 브랜치 동시 |
| 06 | 격리·런타임 | Sysbox, Compose-per-project, 리소스 한계 |
| 07 | CI/CD·프리뷰 | inner/outer loop, GH Actions 위임, subdomain 프리뷰 |
| 08 | Web IDE UX | dockview·Monaco·xterm, 채팅 UX, diff/패치 |
| 09 | 보안/권한/관측 | 시크릿, 감사, RBAC, OTel/Langfuse |
| 10 | 기술 스택 결정 | 결정 매트릭스, 라이선스 함정 체크리스트 |
| 11 | 로드맵 | M0~M5 마일스톤, 비-목표 해제 시점 |
| 12 | Geny 케이스 스터디 | Geny를 첫 어댑트할 때 구체적 작업 단계 |

이 12개 문서는 *서로 참조*하며 모순 없이 일관됨을 목표로 한다. 충돌이 발견되면 *번호가 낮은 문서가 상위 결정*이며 높은 번호 문서가 그것을 어겨선 안 된다.
