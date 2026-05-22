# 01. 시장 / 경쟁 풍경 (Market Landscape)

> **상위 문서**: [00_overview.md](00_overview.md)
> **다음 문서**: [02_use_cases_and_personas.md](02_use_cases_and_personas.md)
> **본 문서의 데이터 기준일**: 2026-05-22

이 문서는 GAPT(Geny Adapted Project Toolkit)와 부분적으로 겹치는 모든 영역의 **경쟁 / 인접 / 부품 제품**을 정리하고, 우리의 *비어 있는 자리*를 정량적으로 정의한다. 위협·기회·차용해야 할 패턴·피해야 할 함정을 결론으로 도출.

---

## 1.1 다섯 카테고리로 자르기

겹치는 영역이 광범위해서, *축*을 먼저 고정한다.

| 카테고리 | 대표 | 우리와의 관계 |
|---|---|---|
| **A. AI 코드 에이전트 / IDE** | Cursor, Windsurf, Aider, Cline, Continue, OpenHands, Devin, Cody, Tabby, Zed | **편집** 영역 직접 경쟁 |
| **B. AI 풀스택 생성/배포** | Bolt.new, Lovable, v0.app, Replit Agent, Tempo | *옆 카테고리* — "생성" 중심, 우리는 "기존 레포" 중심 |
| **C. 셀프호스트 PaaS / 배포** | Coolify, Dokploy, CapRover, Dokku, Kamal, Easypanel, Komodo | **배포** 영역 직접 경쟁 (하지만 AI 없음) |
| **D. 클라우드 개발 환경(CDE) / 샌드박스** | Gitpod/Ona, Coder, Daytona, Codespaces, DevPod, e2b, Modal, Northflank | **워크스페이스** 영역. 일부는 우리가 *부품으로* 빌려옴 (e2b 등) |
| **E. 셀프호스트 Git/CI 인프라** | Gitea, Forgejo, Woodpecker, Drone, Jenkins X, ArgoCD | **부품** — 우리가 위에 얹는 인프라 레이어 |

A·C가 직접 경쟁, D는 절반 경쟁/절반 부품, B는 인접, E는 부품이다.

---

## 1.2 카테고리 A — AI 코드 에이전트 / IDE

### A-1. Cursor (Anysphere)
- **가치**: AI 네이티브 VS Code 포크. Composer, Agent 모드, MCP, 인-에디터 PR 리뷰.
- **강점**: 프런티어 모델 라우팅, RL 튜닝 에이전트 안정성, 60% 지연 감소.
- **약점 (= 우리의 빈자리)**: **셀프호스트 불가**. 5+ 인 팀에서 자체 모델 사용 시 비용 비효율. **배포 없음** — IDE에서 끝난다.
- **라이선스**: 폐쇄. Hobby Free, Pro $20, Pro+ $60, Ultra $200, Teams $40.
- **우리에게**: 살아있는 *UX 기준선*. 우리 웹 콘솔이 Cursor 데스크탑보다 *덜* 매끄러우면 사용자가 이탈한다.

### A-2. Windsurf (Cognition 소속)
- **가치**: Cascade 에이전트 + 전 코드베이스 컨텍스트.
- **강점**: SWE-1.5 자체 모델 (Sonnet 4.5 대비 13× 빠름), SWE-grep 컨텍스트 검색.
- **약점**: SaaS-only. 2025.12 Cognition 인수로 Devin과 통합 방향 불확실.
- **라이선스**: 폐쇄, Pro $20/월.

### A-3. GitHub Copilot Workspace / Agent
- **가치**: 이슈→PR 비동기 워크플로 + Agent Mode (VS Code/JetBrains GA 2026.03).
- **강점**: GitHub와 가장 깊은 통합. Copilot Coding Agent 2025.09 GA. Agent HQ.
- **약점 (= 우리의 빈자리)**: **GitHub 종속**. GHES 옵션이 있지만 SaaS 모델 강제. 외부 Git/Gitea/멀티 레포 시나리오에 부적합.
- **라이선스**: $10~$39/유저/월.

### A-4. Zed 1.0 (Zed Industries)
- **가치**: Rust로 만든 최고 속도 협업 에디터. 2026.04.29 1.0 출시.
- **강점**: CRDT 협업을 사람·AI가 공유. ACP(Agent Client Protocol)로 Claude Agent/Codex/OpenCode 연결. MCP. 통합 diff 리뷰.
- **약점 (= 우리의 빈자리)**: **데스크탑 에디터** — 웹 IDE 아님. 배포·CI 없음. 서버 공유 모델 아님.
- **라이선스**: GPLv3 (협업 백엔드는 SaaS).
- **우리에게**: **ACP 표준**은 우리가 Phase 2~3에서 채택할 가치가 있다 — Cursor/Zed 사용자가 우리 백엔드에 ACP로 붙을 수 있다면 강력한 분배 채널.

### A-5. Aider
- **가치**: 터미널 페어 프로그래머. git-first.
- **강점**: 자동 git commit, watch 모드, 거의 모든 모델(Claude/GPT/Gemini/DeepSeek/Grok/Ollama) 지원. 25k+ stars.
- **약점**: CLI only — UI 없음. 단일 레포 위주. 멀티 프로젝트/배포 없음.
- **라이선스**: Apache 2.0.
- **우리에게**: **참고 자료**. Anthropic 외 모델 지원이 필요해지면, *GAPT에 Aider 어댑터를 만드는 게 아니라* geny-executor 본체에 일반화된 model-router를 PR로 넣어 흡수 ([[feedback_extend_executor_not_adapter_layer]]).

### A-6. Cline / A-7. Roo Code
- **가치**: VS Code 내 오토노머스 에이전트 사이드바.
- **강점**: Cline은 Plan/Act, 30+ LLM 프로바이더, MCP 선구자, 5M+ 설치. Roo Code는 Cline fork로 멀티-모드 + side-by-side diff + 토큰 30% 절약 diff-edit.
- **약점**: **VS Code 확장 한정.** 멀티 사용자/서버 모드 없음. 배포 없음.
- **라이선스**: Apache 2.0.
- **우리에게**: **diff-edit / Plan-Act UX 패턴 차용**. 자율성과 사용자 게이트의 균형 모범.

### A-8. Continue.dev
- **가치**: 완전 OSS + 셀프호스트 가능한 AI 코딩 어시스턴트.
- **강점**: 모든 레이어(chat/edit/autocomplete/embeddings/indexing)가 사용자 엔드포인트로 라우팅. Ollama·vLLM·TGI·Bedrock 자유 연결. 단일 JSON 설정.
- **약점**: **에디터 확장**. *호스트되는 IDE 서버*는 없다. CI/CD/멀티 프로젝트 콘솔 없음.
- **라이선스**: Apache 2.0.
- **우리에게**: **"모든 레이어 pluggable + Apache 2.0"** 철학을 그대로 채용. → V5 (BYO Everything).

### A-9. OpenHands (구 OpenDevin, All-Hands-AI)
- **가치**: 오픈소스 Devin 대안. 자율 에이전트가 실 엔지니어링 작업 수행.
- **강점**: 2026.03 v1.6.0 K8s 지원, Planning Mode, headless REST API, **셀프호스트 Helm 차트**, GitHub/GitLab/CI/Slack 네이티브 통합. $18.8M Series A.
- **약점 (= 우리의 빈자리)**: **IDE-like 라이브 편집 UX 약함** — *에이전트 작업 콘솔* 중심. **PaaS 배포 기능 없음** (연결만, 호스팅 안 함).
- **라이선스**: MIT (코어) + source-available Cloud Helm.
- **우리에게**: **가장 직접적인 경쟁자.** 우리는 *PaaS 배포 통합 + Cursor-like 라이브 UX*가 차별점.

### A-10. Devin (Cognition)
- **가치**: 완전 자율 AI 엔지니어. Slack 트리거.
- **약점**: SaaS-only. ACU(15분 단위) 과금 부담. 사용자가 *위임*하는 모델 — IDE-like 아님.

### A-11. Sweep AI
- **약점**: GH 이슈→PR 단일 워크플로. Copilot Agent/OpenHands에 잠식. 모멘텀 감소.
- **시사점**: *"좁은 단일 워크플로 함정"*의 교과서 사례 — 우리가 피할 것.

### A-12. Cody (Sourcegraph)
- **가치**: 엔터프라이즈 코드 검색 + AI. 셀프호스트 가능. BYO LLM 키.
- **약점 (= 우리의 빈자리)**: 2026년 **Free/Pro 단종**, $59/유저/월 엔터프라이즈 전용. 개인 진입 불가. 배포 없음.
- **시사점**: 솔로/소규모 → 엔터프라이즈만 좁히기 함정. 우리는 반대 방향 유지.

### A-13. Tabby
- **가치**: 셀프호스트 GitHub Copilot 대안.
- **강점**: 단일 바이너리, DB 없음, 12+ IDE 통합, 로컬 모델(CodeLlama/StarCoder/Qwen/DeepSeek), LDAP/SSO, RAG. Pochi 에이전트로 GH 이슈→PR.
- **약점**: *자동완성·인라인 채팅* 중심. 멀티 프로젝트 IDE + CI/CD 콘솔 아님.
- **라이선스**: Apache 2.0.

---

## 1.3 카테고리 B — AI 풀스택 생성/배포

| 제품 | 핵심 | 셀프호스트 | 우리와의 차이 |
|---|---|---|---|
| **Bolt.new** (StackBlitz) | WebContainers 브라우저 내 Node 런타임. Claude Opus, Figma import, Netlify 배포 직결 | ❌ | *생성* 중심, *Netlify* 배포 종속 |
| **Lovable** | 자연어→TS/React + Supabase. Agent Mode 자율, GitHub 양방향 sync | ❌ | *생성* 중심. 새 앱에 최적, 기존 프로덕션 레포에 부적합 |
| **v0.app** (Vercel) | Generative UI → Next.js 샌드박스. Git 패널, Supabase/Snowflake/AWS | ❌ | **Vercel 인프라 종속**. Next.js 편향 |
| **Replit Agent** | 브라우저 안에서 빌드·DB·배포 일원화. Agent 4 병렬 | ❌ | Replit 클라우드 종속. 자기 서버 없음 |
| **Tempo** | 비주얼 React 에디터 → prompt-to-app | ❌ | 디자인-코드 단일화. 신규 앱 |
| **Create.xyz** | 일반 사용자용 vibe coder | ❌ | 초보 시민개발자 |

**공통 패턴**: 모두 *클라우드 SaaS + 새 앱 생성*. 우리의 시나리오(*자기 서버 + 기존 외부 레포*)와는 **사실상 비-경쟁** — 다만 *UX 기대치*는 학습할 부분이 있다 (즉시-실행 프리뷰, 한 클릭 배포).

---

## 1.4 카테고리 C — 셀프호스트 PaaS / 배포

| 제품 | 강점 | 약점 | 라이선스 |
|---|---|---|---|
| **Coolify** | Compose 그대로, 280+ 원클릭, GitHub/GitLab/Gitea push 트리거, Traefik, Let's Encrypt. 2GB VPS에서 동작. | **AI 없음** — 배포만. | Apache 2.0 |
| **Dokploy** | Coolify 동등 + 350+ 템플릿, 자동 백업, Swarm, preview deploy, Okta/Azure SSO. | 동일 — 배포만. | TS, OSS |
| **CapRover** | 가벼운 Docker+NGINX. | Compose 지원 제한, Swarm 의존. | Apache 2.0 |
| **Dokku** | git-push-native, <1GB RAM. | UI 없음, 단일 서버. | MIT |
| **Kamal** (Basecamp) | SSH + Docker 무중단 배포, K8s 없이. HEY.com 프로덕션. | **대시보드 없음**, AI 무관. | MIT |
| **Easypanel** | 모던 패널, Heroku Buildpacks. | 일부 상용. | mixed |
| **Komodo** | Rust 멀티-호스트, Git-driven, audit trail. | Portainer 대안 포지션, AI 없음. | GPLv3 |
| **Portainer** | 1M+ 사용자, K8s/Docker/Swarm. | BE 페이월, AI 무관. | mixed |

**우리에게**: Coolify가 가장 영감원이다 — *Compose 그대로 받기*, *GitHub push 트리거*, *2GB VPS 동작*. 우리는 이 위에 **AI 에이전트 레이어**를 얹는다.

---

## 1.5 카테고리 D — CDE / 샌드박스

| 제품 | 변화 / 강점 | 우리와의 관계 |
|---|---|---|
| **Gitpod → Ona** (2025.09) | "AI 엔지니어링 에이전트 미션 컨트롤"로 피벗. Classic 2025.10.15 종료. Flex는 **AWS 한정 셀프호스트** | **시그널**: CDE 시장이 AI 에이전트 인프라로 재편 중. 우리도 같은 흐름이지만 *AWS 한정 좁힘*은 피해야 함 |
| **Coder.com** | 엔터프라이즈 셀프호스트 CDE + 2026.05 **Coder Agents** (AI 모델 + 셀프호스트 agnostic 에이전트 오케스트레이션) | **가장 직접적 경쟁자.** 그러나 엔터프라이즈 가격·K8s/Terraform 의존·PaaS 배포 약함 |
| **Daytona** | 오픈소스 → AI 에이전트 인프라 피벗. **90ms 환경 생성**. 63.9k stars (Gitpod 13.6k의 4.7×) | AGPL — 부품으로 fork는 부담. 속도 영감 |
| **GitHub Codespaces** | GitHub 종속 클라우드 dev container | GH-only, 비쌈, 셀프호스트 불가 |
| **DevPod** (Loft) | 클라이언트-only OSS, devcontainer.json 표준 | 도구. 멀티 사용자/AI/CI 없음. 우리 *부품 후보* |
| **e2b.dev** | AI 에이전트 코드 실행 샌드박스, **Firecracker microVM**. Apache-2.0 | **부품 후보** — 우리의 M4+ 격리 강화에 차용 가능 |
| **Modal** | gVisor 샌드박스 + 추론·트레이닝·배치 | 인프라 SaaS. 우리 영역 아님 |
| **Northflank Sandboxes** | Kata + gVisor microVM. **BYOC 셀프서브**. SOC 2 Type 2. | 인프라 플랫폼. 운영자 관점 |

**핵심 통찰**: 이 카테고리 전체가 *Gitpod→Ona, Coder→Coder Agents, Daytona→AI 인프라 피벗*으로 일제히 움직였다. **CDE는 더 이상 사람 단독 사용을 위한 게 아니라 사람+에이전트 공동 워크스페이스가 된다는 신호.** 우리는 이미 그 방향에 서 있다.

---

## 1.6 카테고리 E — 셀프호스트 Git/CI 인프라

| 제품 | 강점 / 비고 |
|---|---|
| **Gitea + Actions** | 셀프호스트 Git 사실상 표준. GH Actions 호환. MIT. **2024.02 하드포크 이후 영리법인 인수** — 일부 우려 |
| **Forgejo** | Gitea 비영리(Codeberg) fork. 2024.08부터 GPLv3+ 카피레프트. 빠른 혁신 |
| **Woodpecker** | Drone fork. .woodpecker.yml + Docker step. 서버+에이전트 <50MB RAM. **Apache 2.0** |
| **Drone** | Harness 인수 후 모멘텀 ↓ |
| **Jenkins X** | K8s 네이티브, Tekton. 복잡 |
| **ArgoCD** | GitOps CD 표준. K8s 한정 |

**우리에게**: 경쟁이 아니라 *내장/통합 부품*. 외부 GitHub로 충분(1단계) → Forgejo 옵션 (M3+) → Woodpecker 임베드 (오프라인/에어갭 사용자, M3+).

---

## 1.7 포지셔닝 맵

X축: **셀프호스트 ←→ 클라우드 전용**
Y축: **코드 편집 중심 ↑ ↓ 풀스택 배포 중심**

```
                코드 편집 중심
                      ▲
                      │
   Aider              │              Cursor / Windsurf
   Continue.dev       │              Zed
   Cline / Roo Code   │              Copilot Agent
   Tabby              │              Devin
   Cody (ent)         │
                      │              v0.app
   OpenHands (sh)     │              Bolt.new   ← "AI 생성 → 자기들 클라우드"
   Coder Agents       │              Lovable
   ───────────────────┼──────────────────────────────►
                      │              Replit
                      │
                      │              Vercel
   ★ GAPT ★           │              Render / Fly.io
   Coolify            │
   Dokploy            │              GitHub Actions + Cloud Runner
   Kamal              │
   Gitea+Woodpecker   │
                      │
                      ▼
                풀스택 배포 중심
   ◀──────── 셀프호스트              클라우드 전용 ─────────▶
```

### 빈자리 (Blue Ocean) 분석

- **좌상단** (셀프호스트 × 코드 편집): Continue.dev/Tabby/Cline은 *에디터 확장*, Cody는 *엔터프라이즈만*, OpenHands/Coder Agents는 *콘솔이긴 한데 IDE-like 라이브 편집 약함*.
- **좌하단** (셀프호스트 × 배포): Coolify/Dokploy/Kamal — *AI 없음*.
- **★ GAPT는 좌측을 세로로 관통하는 자리** — *AI 편집부터 빌드·테스트·배포까지 하나의 웹 콘솔로 묶은* 단일 셀프호스트 플랫폼은 사실상 존재하지 않는다.

---

## 1.8 "정말 비어 있는가?" — 5-요건 매트릭스 검증

5요건을 **모두** 만족하는 단일 제품을 찾는다:

| 후보 | 셀프호스트 | AI 에이전트 | 멀티 프로젝트 | CI/CD 내장 | IDE-like UI |
|---|:-:|:-:|:-:|:-:|:-:|
| OpenHands | ✅ | ✅ 강 | ✅ | △ (트리거만) | △ (콘솔) |
| Coder Agents | ✅ | ✅ 강 | ✅ | △ | ✅ (워크스페이스 IDE) |
| **GitLab Duo Agent Platform** | ✅ | ✅ | ✅ | ✅ 강 | △ |
| Cody Enterprise | ✅ | △ | ✅ | ❌ | △ |
| Coolify | ✅ | ❌ | ✅ | △ | ❌ |
| Cursor / Windsurf | ❌ | ✅ 강 | △ | ❌ | ✅ 강 |
| **★ GAPT (목표)** | ✅ | ✅ | ✅ | ✅ | ✅ |

5요건을 *실제로* 가진 후보는 **GitLab Duo Agent Platform**이 유일하다. 그러나 다음 세 가지 한계가 우리에게 빈자리를 남긴다:

1. **GitLab 종속**. GitLab Self-Managed Premium/Ultimate 라이선스 필요.
2. **IDE-like 경험이 Cursor 수준 미달**.
3. **외부 GitHub/Gitea 레포가 일급 시민이 아님** — GitLab으로 끌고 와야 함.

---

## 1.9 왜 아직 아무도 만들지 않았는가 (가설)

1. **이질 도메인 결합 비용**: AI 에이전트 회사는 모델·UX에, PaaS 회사는 인프라에, Git/CI 회사는 호스팅에 집중한다. 단일 팀이 셋 다 *프로덕션 품질*로 만들기 매우 어렵다.
2. **셀프호스트의 비즈니스 모델 함정**: SaaS는 사용량 과금이 자연스럽지만, 셀프호스트는 *사용자가 자기 토큰·자기 모델·자기 서버*를 쓰는 순간 마진이 사라진다. 그래서 Cursor·Windsurf·Lovable·Bolt 같은 "잘 팔리는" AI 제품들은 셀프호스트를 거부한다.
3. **타이밍**: Claude Code SDK·MCP·ACP 같은 표준이 2025–2026 사이에야 자리잡았다. 이전엔 통합 플랫폼을 만들 *재료*가 없었다.
4. **OpenHands·Coder Agents도 수렴 중**: 즉 빈자리이지만 *닫히는 중인 빈자리*다. **6–12개월 윈도우**.

이 분석은 *우리가 빠르게 움직여야 한다*는 결론으로 귀결된다 — 단, *깊이 없이 넓게* 가면 GitLab Duo의 약점(IDE-like 경험 부족)을 우리가 그대로 반복한다. **깊이 우선** + 점진 확장이 답.

---

## 1.10 빌려와야 할 베스트 프랙티스 5가지

| # | 출처 | 패턴 | 우리에게의 적용 |
|---|---|---|---|
| 1 | **Cline / Roo Code** | Plan/Act 워크플로 + side-by-side diff + diff-edit 토큰 절약 | [08_web_ide_ux.md](08_web_ide_ux.md)의 채팅 UX 기본 |
| 2 | **Coolify** | "Compose 그대로 받기" + GitHub push 자동 배포 + 2GB VPS 동작 | [07_cicd_and_preview.md](07_cicd_and_preview.md) outer loop |
| 3 | **Continue.dev** | "모든 레이어 pluggable + Apache 2.0" | [10_tech_stack_decisions.md](10_tech_stack_decisions.md) 어댑터 인터페이스 |
| 4 | **OpenHands** | source-available Helm 차트, 헤드리스 REST API | [11_roadmap.md](11_roadmap.md) M3 배포 형태 |
| 5 | **Zed** | ACP(Agent Client Protocol) 표준 채택 | 우리 백엔드가 외부 IDE에서도 접근 가능 (M3+) |

---

## 1.11 피해야 할 함정 5가지

| # | 함정 | 사례 | 우리의 방어선 |
|---|---|---|---|
| F1 | **단일 기능 함정** | Sweep AI (GH 이슈→PR만) → Copilot Agent에 잠식 | 연결된 *흐름 전체*를 잡는다 (편집·테스트·리뷰·배포·관측) |
| F2 | **셀프호스트 좁힘** | Gitpod Flex → AWS 한정 → 기존 사용자 이탈 | 2GB VPS에서도 동작하는 최소 사양 유지 |
| F3 | **엔터프라이즈 전용으로 좁히기** | Sourcegraph Cody → Free/Pro 단종, $59/유저/월 | 솔로 무료 셀프호스트 코어 영구 보장 |
| F4 | **벤더 클라우드 종속 배포** | Bolt→Netlify, v0→Vercel, Replit→Replit Cloud | 사용자 *자기 인프라*에 배포 |
| F5 | **자체 모델/마켓플레이스 야망** | (다수 사례) — 모델 가치는 외부 SDK에 흡수됨 | 모델은 어댑터로만, 우리 가치는 *오케스트레이션·격리·UX*에 있다 |

---

## 1.12 시장 측면의 결론

> **우리의 한 줄 포지셔닝**: *"내 서버에 띄우는 OpenHands × Coolify의 합본 — Cursor 같은 라이브 편집 UI로 내 외부 레포(GitHub/Gitea)를 Claude Code로 작업하고, 그대로 같은 화면에서 빌드·테스트·내 인프라 배포까지."*

이 한 줄을 완전히 만족시키는 단일 제품은 2026.05 현재 **존재하지 않는다.** 가장 가까운 GitLab Duo는 GitLab 안에서만 성립한다. OpenHands·Coder Agents·Coolify·Continue.dev 각자가 우리 청사진의 25–40%를 들고 있고, 그 사이의 *수직 통합* + *솔로~소규모 팀 가격대*가 우리의 **6–12개월 시장 윈도우**다.

이후 문서는 이 윈도우 안에 들어가기 위한 **기술적 결정**들을 다룬다.

---

## 1.13 부록: 카테고리 한눈에 보기

```
                 │ 셀프호스트 │ AI 에이전트 │ 멀티 프로젝트 │ CI/CD │ IDE-like UI │ 우리와의 관계
─────────────────┼──────────┼───────────┼───────────┼──────┼───────────┼────────────
Cursor           │    ❌    │    ✅✅    │     △     │  ❌  │    ✅✅    │ UX 기준선
Windsurf         │    ❌    │    ✅✅    │     △     │  ❌  │    ✅✅    │ UX 기준선
Zed              │    △     │    ✅     │     △     │  ❌  │    ✅✅    │ ACP 표준 차용
Aider            │   (CLI)   │    ✅     │     ❌    │  ❌  │    ❌     │ 2차 어댑터
Continue.dev     │    ✅    │    ✅     │     △     │  ❌  │    ❌(ext) │ pluggable 철학
Cline / Roo Code │    ❌    │    ✅     │     △     │  ❌  │    ❌(ext) │ Plan/Act UX
Cody Enterprise  │    ✅    │    △      │     ✅    │  ❌  │    △      │ 엔터프라이즈 좁힘 함정
Tabby            │    ✅    │    △      │     △     │  ❌  │    ❌(ext) │ 자동완성 한정
OpenHands        │    ✅    │    ✅✅    │     ✅    │  △  │    △      │ 직접 경쟁(코드)
Devin            │    ❌    │    ✅✅    │     △     │  △  │    ❌     │ 위임 모델 — 다른 분류
Copilot Agent    │    ❌    │    ✅     │     ✅    │  ✅  │    △      │ GitHub 종속
GitLab Duo       │    ✅    │    ✅     │     ✅    │  ✅✅ │    △      │ 5요건 가장 근접
Bolt.new         │    ❌    │    ✅     │     ❌    │  △  │    ✅     │ 생성 카테고리
Lovable          │    ❌    │    ✅     │     ❌    │  △  │    ✅     │ 생성 카테고리
v0.app           │    ❌    │    ✅     │     ❌    │  ✅  │    ✅     │ 생성+Vercel 종속
Replit Agent     │    ❌    │    ✅     │     △     │  ✅  │    ✅     │ Replit 종속
Coolify          │    ✅    │    ❌     │     ✅    │  △  │    ❌     │ 직접 경쟁(배포)
Dokploy          │    ✅    │    ❌     │     ✅    │  △  │    ❌     │ 직접 경쟁(배포)
Kamal            │   (CLI)   │    ❌     │     ✅    │  △  │    ❌     │ Compose-less 영감
Gitpod / Ona     │    △     │    ✅     │     ✅    │  △  │    ✅     │ AWS 한정 (함정)
Coder.com        │    ✅    │    ✅     │     ✅    │  △  │    ✅     │ 직접 경쟁(엔터)
Daytona          │    ✅    │    △      │     ✅    │  ❌  │    △      │ 90ms 부팅 영감
DevPod           │    ✅    │    ❌     │     ✅    │  ❌  │    △      │ 부품 후보
e2b.dev          │    △     │   (부품)   │    N/A    │  ❌  │    ❌     │ 부품 후보(M4+)
Coolify+OpenHands(가설) │ ✅  │   ✅    │     ✅    │   ✅  │   △       │ ← *우리가 만들려는 것*
★ GAPT           │    ✅    │    ✅     │     ✅    │  ✅  │    ✅     │ 자리
```

---

## 1.14 출처 (Sources)

Agent A의 웹 리서치 결과 풀 인용. 핵심 출처만 추려서:

- [Cursor AI 2026 Review (WeavAI)](https://weavai.app/blog/en/2026/04/24/cursor-ai-2026-review-features-pricing-worth/)
- [Windsurf Review 2026 (Taskade)](https://www.taskade.com/blog/windsurf-review)
- [GitHub Copilot Agent Mode press release](https://github.com/newsroom/press-releases/agent-mode)
- [Zed 1.0 Launch](https://conzit.com/post/zed-10-launch-a-new-era-in-code-editing-and-collaboration)
- [OpenHands Cloud Self-hosted](https://www.openhands.dev/blog/openhands-cloud-self-hosted-secure-convenient-deployment-of-ai-software-development-agents)
- [Devin 2.0 Review](https://weavai.app/blog/en/2026/05/13/devin-2-0-review-2026-ai-engineer-price-drops-to-20/)
- [Sourcegraph Cody Pricing](https://sourcegraph.com/pricing)
- [Tabby GitHub](https://github.com/TabbyML/tabby)
- [Bolt.new](https://bolt.new/), [Lovable](https://lovable.dev/), [v0 by Vercel](https://v0.app/)
- [Coolify](https://coolify.io/), [Dokploy](https://dokploy.com/)
- [Kamal](https://kamal-deploy.org/)
- [Coder Agents announcement](https://coder.com/blog/self-hosted-ai-model-agnostic-coder-agents)
- [Anthropic engineers running Claude Code remotely (Coder)](https://coder.com/blog/building-for-2026-why-anthropic-engineers-are-running-claude-code-remotely-with-c)
- [Daytona vs Gitpod 2026](https://openalternative.co/compare/daytona/vs/gitpod)
- [Self-Hosted Git Platforms 2026 (dasroot.net)](https://dasroot.net/posts/2026/01/self-hosted-git-platforms-gitlab-gitea-forgejo-2026/)
- [Self-Host Woodpecker CI 2026 (OSSAlt)](https://ossalt.com/guides/self-host-woodpecker-ci-2026)
- [GitLab Duo Agent Platform](https://about.gitlab.com/gitlab-duo-agent-platform/)
- [E2B](https://e2b.dev/), [Northflank Sandboxes](https://northflank.com/product/sandboxes)
- [Continue.dev](https://www.continue.dev/), [Continue self-host guide (Spheron)](https://www.spheron.network/blog/self-host-ai-coding-assistant-gpu-cloud/)
- [Self-Hosting AI Agents for Regulated Enterprises (Baytech)](https://www.baytechconsulting.com/blog/keep-code-off-cloud-self-hosted-ai-dev-agents)
