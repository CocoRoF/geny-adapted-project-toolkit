# 02. 유스케이스 / 페르소나 (Use Cases & Personas)

> **상위**: [00](00_overview.md) / [01](01_market_landscape.md)
> **다음**: [03_system_architecture.md](03_system_architecture.md)

이 문서는 GAPT가 *누구를 위해, 어떤 상황에서, 어떤 결과를 만드는지*를 페르소나 카드 → 골든패스 시나리오 → 비-시나리오 순으로 정리한다. 이후 모든 기능 결정은 여기 정의된 *시나리오 단위의 통점*으로 정당화되어야 한다.

---

## 2.1 페르소나 카드

### P1. **솔로 호비스트 / 인디 개발자 (1차 사용자)**

```
이름: 한 (가상). 30대 한국 개발자, 본업+사이드 프로젝트 다수.
서버: Hetzner CX22 (4vCPU/8GB) 1대 + 집에 있는 미니PC 1대
운영 중: Geny, geny-avatar, 개인 블로그, 1~2개 실험 레포
이미 쓰는 것: Docker Compose, GitHub Actions, Anthropic API, Claude Code(로컬)
연 예산: ~$200 인프라 + ~$50/월 LLM
```

**목표**: 여러 사이드 프로젝트를 *일관된 인터페이스*로 운영. 출장 중에도 폰/태블릿으로 한 줄 고치고 배포.

**통점**:
- 매 프로젝트가 다른 compose, 다른 CI, 다른 secret 위치 → 매번 SSH 들어가서 기억 더듬음.
- 데스크탑 Cursor가 좋지만 *서버에서 돌아가는 LLM 작업*은 불가능.
- 비용이 어디서 새는지 모름 (3개 프로젝트의 LLM 토큰이 다 한 키로 합산).

**Definition of Success**: GAPT 하나에 4개 프로젝트 등록, 매일 1~2회는 콘솔에서 작업, 데스크탑 Cursor 의존도 50% 이하로 감소.

**라이선스 / 비용 감수성**: ★★★★★ (벤더 락인·구독 거부, OSS 강력 선호)

---

### P2. **소규모 팀 리드 (2단계 1차 사용자, 1단계 fast-follow)**

```
이름: 김 (가상). 4명 팀의 기술 리드.
서버: 회사 AWS 1대 + 자체 데이터센터 1대
운영 중: 5~8개 마이크로서비스 + 1개 프론트
이미 쓰는 것: GitHub Enterprise(또는 self-hosted GitLab), Compose, GH Actions
연 예산: 회사 카드 (LLM 월 ~$500)
```

**목표**: 팀이 *AI 에이전트를 안전하게 공유*. 누가 무엇을 시켰는지, 어떤 변경이 일어났는지, 비용이 어떻게 분배되는지 *감사 가능*.

**통점**:
- Cursor를 4명이 쓰면 월 $160 + LLM 토큰 별도. 멀티 사용자 거버넌스/감사 없음.
- 신참이 AI에게 잘못된 명령(예: 마이그레이션 prod 실행)을 내릴 가능성에 대한 두려움.
- 사내 정책상 코드가 SaaS 벤더 컨테이너에 머무르는 게 점점 부담.

**DoS**: 팀 4명이 같은 콘솔에서 작업, 프로젝트별 권한 분리, 매월 1회 LLM 사용 리포트 자동 생성.

**라이선스 / 비용 감수성**: ★★★★ (회사 카드는 있지만 *벤더 종속*은 거부)

---

### P3. **사내 플랫폼 엔지니어 (M4+ 일급 지원, 지금은 *막지 않도록* 설계)**

```
이름: Lee (가상). 50명 엔지니어링 조직의 플랫폼 팀.
인프라: K8s 멀티 클러스터, Vault, SSO(Okta), 사내 Git(GitLab Self-Managed)
목표: "개발자가 IDE 안에서 안전하게 AI를 쓰게 하자" + "감사 통과 가능"
```

**목표**: GAPT를 *플랫폼 컴포넌트*로 사내에 배포. 사용자가 SSO로 들어와서 자기 팀 프로젝트만 보고, 모든 LLM 통화/도구 호출이 SIEM으로 흐름.

**우리의 입장 (Phase 0~M3)**: *지금은 P3를 1차 사용자로 만들지 않는다.* 그러나 다음 아키텍처 원칙이 깨지면 P3는 M4에서 만족 불가능:

| 원칙 | 깨지면 P3 불가능 |
|---|---|
| RBAC 모델이 처음부터 (User → Org → Project → Env) 구조 | 단일 사용자 가정으로 짜면 retrofit 큼 |
| Auth가 IDP-pluggable 인터페이스 뒤에 | 단일 magic-link로 박으면 SSO 추가 어려움 |
| 모든 감사 이벤트가 구조화 (JSON, ID 부여) | 자유 텍스트 로그는 SIEM 못 옴 |
| 격리 모델이 멀티-사용자 안전 | 단일 사용자에서만 안전하면 멀티 사용자 시 재설계 |

→ [03](03_system_architecture.md), [09](09_security_authz_observability.md), [11](11_roadmap.md)에서 다시 다룸.

---

### P4. **에이전트 자동 운영자 (미래, M5+)**

```
이름: (없음). 사람이 거의 개입하지 않고 에이전트끼리 PR을 만들고 머지하는 모드.
시나리오: "매일 새벽 의존성 업데이트 PR + 테스트 + 자동 머지" 류.
```

이건 *지금은 의도적으로 비-목표*. 그러나 우리가 만드는 LLM 에이전트 세션 API가 *Slack/이메일/cron 트리거*로도 호출 가능하도록 *처음부터* 헤드리스 모드 1급이어야 한다.

---

## 2.2 페르소나별 우선순위 매트릭스

| 기능 | P1 (솔로) | P2 (팀) | P3 (사내) | P4 (자동) |
|---|:-:|:-:|:-:|:-:|
| 단일 노드 Docker Compose 배포 | ★★★ | ★★ | ★ | ★ |
| Sysbox 격리 (호스트 안전) | ★★★ | ★★★ | ★★★ | ★★★ |
| 멀티 프로젝트 사이드바 | ★★★ | ★★★ | ★★ | ★ |
| Claude Code SDK 통합 | ★★★ | ★★★ | ★★ | ★★★ |
| Cursor-급 라이브 편집 UI | ★★★ | ★★★ | ★★ | ★ |
| GitHub OAuth + PAT 보관 | ★★★ | ★★★ | ★ (SSO 우선) | ★★ |
| 비용 가시화 (프로젝트별 토큰) | ★★ | ★★★ | ★★★ | ★★ |
| RBAC (Org → Project → Env) | ★ | ★★★ | ★★★ | ★ |
| SSO (Authentik/OIDC) | — | ★ | ★★★ | — |
| 헤드리스 REST API (cron/Slack 트리거) | ★ | ★★ | ★★ | ★★★ |
| 감사 로그 (구조화 JSON, SIEM 친화) | ★ | ★★ | ★★★ | ★★ |
| K8s 멀티 노드 백엔드 | — | ★ | ★★★ | ★★ |
| 사내 Forgejo 임베드 | — | ★ | ★★ | ★ |
| GitOps (ArgoCD/Flux 통합) | — | ★ | ★★ | ★ |
| LLM 모델 라우터 (Anthropic/OpenAI/로컬) | ★★ | ★★ | ★★★ | ★★ |

**해석**: ★★★이 Phase 0~M3에서 *반드시* 만족, ★★는 M3~M5, ★는 M5+ 또는 옵셔널.

---

## 2.3 골든패스 시나리오 5개

### G1. **첫 프로젝트 등록 (Onboarding)**

```
[T+0]   사용자가 docker run gapt:latest로 토큰 없이 첫 실행. Caddy 자동 HTTPS, basic auth 자동 생성.
[T+1m]  https://toolkit.local 접속. "GitHub 연결" → OAuth Device Flow.
[T+2m]  사용자의 GitHub 레포 목록이 표시됨. "Geny" 선택.
[T+2m]  GAPT가 호스트에 Sysbox 컨테이너 1개 부팅, 그 안에서 git clone, compose.dev.yml detect.
[T+3m]  "compose.dev.yml을 사용해서 부팅합니다 [예/아니오/수정]" 사용자 확인 후 docker compose up -d.
[T+4m]  포트 매핑 자동 인식 → preview URL: https://geny.preview.toolkit.local 생성.
[T+5m]  사용자가 "프로젝트 컨텍스트 자동 생성" 클릭 → Claude Code가 레포를 훑고
        plan.md, progress/ 폴더 + (감지된) CLAUDE.md를 요약해 좌측 패널에 표시.
[T+6m]  완료. 채팅 입력창에 커서.
```

**중요한 디테일**:
- 5분 안에 *첫 번째 LLM 메시지를 보낼 수 있어야* 한다.
- 사용자가 compose 파일을 수정하지 않아도 동작해야 한다 (Coolify 정신).
- 자동으로 잡지 못한 부분이 있으면 *명시적으로 묻고 진행*. 추측해서 실패하지 말 것.

---

### G2. **AI 페어 코딩 사이클 (Daily Driver)**

```
사용자(P1): 등록된 Geny 워크스페이스에 입장.

[T+0]   "agent_session_manager.py에 idempotent 재시도 추가하고 unit test도 작성해줘"
[T+5s]  에이전트 응답 스트림 시작. Plan 모드로 4단계 계획 표시.
[T+30s] 사용자가 계획 검토 후 "Act" 클릭.
[T+1m]  에이전트가 read → edit → run pytest 순으로 진행. 진행 상황 라이브 표시.
[T+2m]  pytest 1개 실패. 에이전트가 자동으로 수정 시도.
[T+3m]  성공. 우측 dockview에 5개 파일 side-by-side diff 표시.
[T+3m+] 사용자가 4개 승인, 1개는 더 작게 만들라고 요청.
[T+4m]  최종 diff에서 사용자가 "Commit & PR" 클릭.
[T+4m+] 새 브랜치 자동 생성, 커밋 메시지 에이전트가 작성, push, gh CLI로 PR 생성.
[T+5m]  GitHub Actions 자동 실행. 결과가 우측 패널에 라이브 스트림.
[T+10m] CI 그린. 사용자가 머지.
```

**핵심**: 이 5분이 *데스크탑 Cursor + 별도 터미널 + GitHub 웹*보다 빠르고 끊김 없어야 한다. 안 그러면 P1은 데스크탑으로 돌아간다.

---

### G3. **다중 워크트리 동시 작업 (Multi-Branch)**

```
사용자(P1): 같은 Geny 프로젝트에서 두 가지 작업을 병행.

[Tab A]  main 브랜치 워크트리에서 hotfix 작업 중.
         compose stack A → geny.preview.toolkit.local:8001
[Tab B]  feat/avatar-integration 워크트리에서 새 기능.
         compose stack B → geny-feat-avatar.preview.toolkit.local:8002

같은 토큰, 같은 .git, 같은 컨테이너 — 단 *worktree 디렉토리만 다름*.

사용자가 Tab A에서 hotfix 머지 후 Tab B로 돌아오면, 에이전트는
"main이 업데이트됐습니다. rebase 하시겠어요?"를 자동으로 묻는다.
```

**핵심**: 한 프로젝트에 *여러 활성 워크트리*가 가능. 각 워크트리가 자기 compose 스택을 가진다. 포트 충돌은 toolkit이 자동 할당.

→ [05](05_git_workflow.md), [06](06_isolation_and_runtime.md)에서 디테일.

---

### G4. **배포 (Outer Loop)**

```
[T+0]   사용자: "main에 머지됐으니 prod에 배포하자"
[T+5s]  에이전트가 현재 환경 정의 + 정책 결과를 표시:
        - target: prod-vps (사용자 등록 인프라)
        - method: docker compose pull && up -d
        - secrets: .env.prod (Vault에서 주입)
        - hooks: pre-deploy backup, post-deploy smoke-test
        - PolicyEngine: 'deploy.prod' → require_user_approval + 2FA (기본 정책)
[T+10s] 사용자가 "Deploy" 클릭. TOTP 입력.
[T+1m]  toolkit이 prod-vps에 SSH(또는 등록된 webhook)로 deploy script 실행.
        로그가 라이브 스트림.
[T+2m]  smoke-test 그린, Grafana 메트릭 정상.
[T+2m]  Slack/Discord에 자동 알림 (옵션).
```

**핵심**: 배포가 *별도 도구로 새는 게 아니라* 같은 UI에 머문다. prod 작업의 2-factor는 *기본 정책*이며 PolicyEngine config로 사용자가 조정 가능 ([09](09_security_authz_observability.md) §9.2.3).

---

### G5. **헤드리스 / 자동화 시나리오 (M5+, 단 인터페이스는 M0부터)**

```
[crontab] 매일 06:00, /api/projects/geny/sessions POST
          { "preset": "dependency-update", "auto_pr": true }

[06:00]  toolkit이 임시 세션 생성. dependabot 같은 워크플로:
         git fetch, npm/poetry/uv outdated 분석, 안전한 패치만 적용,
         테스트 실행, 그린이면 PR 생성, Slack 알림.
[06:15]  결과가 사용자 다음 로그인 시 "지난밤 활동" 카드로 표시.
```

**핵심**: 인터랙티브 세션과 *완전히 동일한 코드 경로*가 헤드리스에서도 동작. 다른 페르소나 P4를 위한 발판.

---

## 2.4 비-시나리오 (Non-Scenarios)

이 toolkit이 *명시적으로 잘 못하는* 시나리오. 사용자가 시도해도 좋은 경험을 못 받는다 — 다른 도구를 권장.

| 비-시나리오 | 더 좋은 도구 |
|---|---|
| **"프롬프트 한 줄로 새 풀스택 앱 생성"** | Bolt.new / Lovable / v0.app |
| **"내 데스크탑에서 IDE 확장으로 인라인 자동완성"** | Cursor / Continue.dev / Tabby |
| **"파이프라인 한 번 돌리고 끄는 CI 빌더"** | GitHub Actions / Woodpecker 단독 |
| **"K8s 클러스터 GitOps 동기화"** | ArgoCD / Flux 단독 (M4+에서 통합 검토) |
| **"사내 모든 코드를 인덱싱하는 코드 검색"** | Sourcegraph Cody |
| **"AI 에이전트가 Slack 채널에 상주하며 잡일 처리"** | Devin / Codex 자체 |
| **"리얼타임 협업 페어 프로그래밍 (사람 2명+)"** | Zed / VS Code Live Share |

비-시나리오를 명확히 함으로써 *우리의 깊이*가 어디로 가는지를 결정한다.

---

## 2.5 우선 채택 사용자 시나리오 (Phase 0 검증용)

Phase 0~M1에서 toolkit이 *최소한 자기 자신을 정당화한다*고 말하려면 다음 두 시나리오가 완전히 동작해야 한다.

### Eat-our-own-dogfood
- toolkit 본체의 코드를 toolkit 자신에서 편집·테스트·배포.
- 즉 toolkit이 자기 GitHub 레포에 등록되어, 자기 자신의 다음 PR을 자신의 워크스페이스에서 만든다.

### Geny 첫 어댑트
- 메모리의 *Geny 통합 cadence* (plan/progress/analysis)에 맞춰 한 사이클을 외부 IDE 없이 완수.
- 자세한 적용 절차는 [12_geny_case_study.md](12_geny_case_study.md).

이 두 시나리오를 *모두* 통과하면 1단계 PMF 신호로 본다.

---

## 2.6 사용자 여정 지도 (Journey Map) — P1

```
Day 1   [발견]       OSS 셋업, 첫 프로젝트 등록, 첫 LLM 응답까지 5분
Day 2~7 [형성]       기존 사이드 프로젝트 2~3개 추가, 매일 1회 사용
Day 8~30[정착]       데스크탑 Cursor 사용량 절반 이하로 감소,
                     배포 작업의 60% 이상이 toolkit에서 일어남
Day 30+ [확장]       cron/webhook으로 야간 자동 PR, 새 노트북에서 즉시 같은 환경 진입
```

**채택 실패 시그널** (Phase 0 단계에서 모니터해야 할 것):
- 첫 LLM 응답까지 10분 이상 (G1 실패)
- 첫 일주일에 두 번째 프로젝트 등록 안 함 (멀티 프로젝트 가치 실패)
- compose 수정을 강제로 요구 (Coolify 정신 위반)
- 사용자가 "이게 Cursor보다 느려서 못 쓰겠다" (UX 실패)

---

## 2.7 페르소나 간 충돌 지점 (Tension)

| 결정 사항 | P1 선호 | P2/P3 선호 | 우리의 1차 결정 |
|---|---|---|---|
| 인증 | 무인증 / basic / magic-link | OIDC / SSO 필수 | M0~M3: magic-link 기본 + IDP-pluggable 인터페이스 |
| 시크릿 보관 | OS keyring 1개 충분 | Vault / SOPS | 인터페이스 추상화, 1차 구현은 keyring |
| RBAC | 불필요 (혼자) | 필수 (감사) | 모델은 처음부터, UI에선 단일 사용자 모드 토글 |
| 라이선스 | 가능한 한 OSS Apache/MIT | 엔터프라이즈 기능 유료 OK | 코어 OSS, 엔터프라이즈 기능은 별도 라인 (M4+) |
| K8s 백엔드 | 부담 | 필요 | Phase 0~M3 Docker Compose 단일 노드, M4 K8s 어댑터 |

**규칙**: *충돌이 있으면 P1을 1차 사용자로*. 단 P2/P3가 *원천 봉쇄되지 않도록* 인터페이스 레이어 보장.

---

## 2.8 결론

GAPT는 P1(솔로 호비스트)에게 *첫날부터* 가치를 주고, P2(소규모 팀)에게 *2단계*에서 가치를 주며, P3(사내 플랫폼)는 *지금 만족시키지 않지만 미래의 일급 지원*을 위해 *지금 막지 않는* 설계로 간다. P4(자동화)는 헤드리스 모드 일급으로 인터페이스만 처음부터 갖춘다.

위 다섯 골든패스 + 자기 자신 도그푸드 + Geny 첫 어댑트가 *Phase 0~M1의 성공 정의*다. 이후 모든 기술적 결정은 이 시나리오 단위 통점으로 정당화된다.
