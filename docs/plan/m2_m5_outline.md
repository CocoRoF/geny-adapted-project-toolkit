# M2~M5 윤곽 (Outline)

> **상위**: [`00_master_plan.md`](00_master_plan.md)
> **이유**: 예측 정확도가 낮은 미래 단계를 *현재 디테일하게 짜는 것은 낭비*. M1 종료 시 학습된 현실로 M2를 디테일화, M2 종료 시 M3 디테일화 — 그 시점에 본 파일은 M3+로 축소된다.

각 단계의 *진입 조건 / 주제 / 작업 카테고리 / DoD / 비-목표 풀림 / 리스크*만 윤곽으로.

---

## M2 — 멀티 프로젝트 + 워크트리 + UX 다듬기

### 진입 조건
- M1-E4 완료 (Dogfood + Geny 어댑트 통과)
- M1 retrospective (analysis/2026XXXX_m1_retrospective.md) 사용자 검토
- *최소 1주일* 일상 사용 (사용자 본인 P1 페르소나)

### 주제
사용자가 *매일* 4개 사이드 프로젝트를 GAPT에서 운영하는 상태. 데스크탑 Cursor 의존도 50% 이하.

### 작업 카테고리 (예상 epic 단위)
1. **M2-E1: 멀티 프로젝트 동시 운영**
   - 좌측 트리 다중 프로젝트, 빠른 전환 (`Ctrl+P`)
   - 프로젝트별 비용 분리 집계 + 대시보드 통합 뷰
   - 동시 활성 sandbox 수 cap + UI 표시
2. **M2-E2: 워크트리 1급 시민화**
   - `git worktree add/remove` 자동
   - 같은 프로젝트 N개 워크스페이스 동시 (compose stack 격리, 포트 자동 할당)
   - Caddy subdomain per worktree (`{branch-slug}.{project}.preview.{domain}`)
3. **M2-E3: 프리뷰 노출 강화**
   - 와일드카드 DNS + Let's Encrypt DNS-01 자동
   - cloudflared 토글 (opt-in)
   - 공유 URL + 만료 + 외부 사용자 인증 옵션
4. **M2-E4: UX 다듬기**
   - dockview 레이아웃 프리셋 4종 안정화 + 사용자 정의 5번째
   - Plan/Act 모드 UX 다듬기 (Roo Code 패턴 학습 결과 반영)
   - diff 카드 그룹핑 + "모두 Approve"
   - 단축키 + 명령 팔레트 확장
   - 다크/라이트 토글
5. **M2-E5: 알림 + 모바일**
   - PWA 알림 (web push)
   - Slack/Discord/Telegram webhook 정식
   - 폰에서 보조 사용 (Approve / Deploy / 채팅 읽기)
6. **M2-E6: 빌드 캐시 최적화**
   - 프로젝트별 inner docker overlay 캐시 효율 (이미지 layer 공유 검토)
   - 언어 cache (npm/uv/cargo) 공유 옵션
   - 빌드 시간 측정 + 목표 < M1 대비 30% 단축

### DoD (M2 → M3 게이트)
- [ ] 4개 프로젝트 일주일 운영, 데스크탑 Cursor 의존도 50% 이하 (사용자 자가 측정)
- [ ] 골든패스 G3 (다중 워크트리), G5 (헤드리스 인터페이스) 동작
- [ ] 외부 친구가 프리뷰 URL로 데모 확인 가능 (cloudflared opt-in)
- [ ] 채팅 UX가 *Cursor에 나쁘지 않음* 수준 (사용자 자체 평가, blind 비교)
- [ ] 비용/Audit 대시보드가 매일 한 번 이상 사용됨

### 비-목표 풀림
- (없음 — 모두 M3 이후)

### 리스크 (윤곽)
- 멀티 프로젝트 동시 = 호스트 자원 폭증 → resource cap UI 필수
- 와일드카드 도메인 DNS 셋업이 사용자 부담 → 가이드 + 자체 서명 모드 fallback
- 모바일 UX가 *과제 범위 폭발* → 보조 사용에 한정

---

## M3 — 멀티 사용자 + OIDC + 옵션 모듈

### 진입 조건
- M2 완료 + 1주일 다중 프로젝트 운영
- (선택) 외부 사용자 한두 명이 GAPT 셋업 시도해서 막힌 점 정리

### 주제
P2 (소규모 팀) 진입. RBAC 완성, SSO, 시크릿 강화, 옵션 모듈.

### 작업 카테고리
1. **M3-E1: 인증 어댑터 확장**
   - `AuthentikIdp` (OIDC) — 셀프호스트 IDP
   - SSO + OIDC discovery, refresh token
   - MFA TOTP 1급 (M1 기본 위에 강화)
   - (옵션) WebAuthn / passkey
2. **M3-E2: RBAC 완성**
   - User → Org → Project → Env 4계층 권한 *전체* 구현 (M1은 기본 정책만)
   - 멤버 초대 / role 변경 API + UI
   - 사용자 정의 role (ABAC 전 단계)
3. **M3-E3: Secret 백엔드 확장**
   - `InfisicalBackend` (셀프호스트)
   - SOPS+age 정식 (CI 친화)
4. **M3-E4: Audit 강화**
   - `prev_hash` 체인
   - Vector → Loki / 외부 SIEM 라우터
   - 일일 체크포인트 서명
5. **M3-E5: 옵션 모듈**
   - **Forgejo** 임베드 (compose 옵션) — 셀프호스트 Git
   - **Woodpecker CI** 임베드 — 오프라인/에어갭 사용자
   - **openvscode-server** iframe 보조 모드 (Open VSX 한정 — L1 라이선스 함정 회피)
6. **M3-E6: 모델 확장 — *executor PR로*** ([[feedback_extend_executor_not_adapter_layer]])
   - geny-executor에 추가 provider PR (Bedrock, Ollama 등)이 필요해지면 그 PR 후 의존 버전 올림
   - GAPT 측엔 manifest UI에서 provider 선택 가능하도록 강화 (사용자가 키 등록만 하면 OK)
7. **M3-E7: LSP 1차**
   - 데몬이 컨테이너 안에서 `pyright` / `tsserver` / `gopls` spawn
   - WebSocket bridge → Monaco languageclient
   - 자동완성, go-to-definition, hover
8. **M3-E8: PWA web push + i18n 확장**
   - 알림에 OS push 통합
   - 추가 언어 (일/중/영 확장)

### DoD (M3 → M4 게이트)
- [ ] 외부 2~3명 사용자(P2 페르소나) 실제 사용, 멤버 권한 분리 동작
- [ ] 감사 데이터 외부 SIEM 라우팅 검증
- [ ] Authentik OIDC SSO 통과
- [ ] (executor에 추가된 provider가 있다면) 그 provider로 한 세션 완주

### 비-목표 풀림
- 사내 Git 호스팅 (Forgejo) — 옵션으로 사용 가능
- openvscode-server iframe — 보조 모드 사용 가능
- 사용자 정의 LLM provider (executor에 추가하는 경로로)

### 리스크 (윤곽)
- 멀티 사용자 → 권한/감사 *대량 회귀* 위험 (모델은 처음부터 owner_id 있지만 UI 회귀 가능)
- 옵션 모듈이 *코어 복잡도 증가* — 활성화는 명시 opt-in
- Forgejo (GPLv3+) 임베드 → 라이선스 가이드 사용자에게 명확

---

## M4 — K8s 백엔드 / 엔터프라이즈 인터페이스

### 진입 조건
- M3 완료
- P3 (사내 플랫폼 엔지니어) 수요 신호 — 실제 사내 엔지니어가 시도 의지 표명

### 주제
멀티 노드 / K8s / 컴플라이언스 준비. P3의 일급 지원 시작.

### 작업 카테고리
1. **M4-E1: SandboxBackend.K8s 구현**
   - Pod = 컨테이너, namespace 격리, NetworkPolicy
   - GAPT 컨트롤 플레인 자체도 K8s 배포 가능 (Helm 차트)
2. **M4-E2: 상태 외부화**
   - PostgreSQL 외부 매니지드/HA (자체 cluster 또는 RDS/CloudSQL)
   - SeaweedFS 멀티노드 (Filer + Volume Server 분리, replication ≥ 2)
3. **M4-E3: 데몬 RPC 네트워크 친화**
   - unix socket → mTLS HTTP
   - 데몬이 K8s Pod 안에서도 동작
4. **M4-E4: 컨트롤 플레인 HA**
   - 다중 replica + leader election (Redis 또는 etcd)
   - 세션 affinity (sticky) 또는 세션 상태 외부화
5. **M4-E5: GitOps 통합**
   - ArgoCD / Flux 어댑터 (DeployTarget)
6. **M4-E6: 컴플라이언스 모듈**
   - SOC 2 증거 수집 자동화
   - 변경 승인 워크플로 (multi-approver)
7. **M4-E7: 엔터프라이즈 IDP**
   - Keycloak / SAML / SCIM provisioning
8. **M4-E8: ABAC 옵션**
   - Casbin / Cedar 정책 엔진 (RBAC 위에 layer)
9. **M4-E9: Helm 차트 + 운영 가이드**
   - source-available Helm (OpenHands 패턴)
   - 운영 SOP, 백업/복구 plan

### DoD (M4 → M5 게이트)
- [ ] 50명 규모 가상 시나리오에서 멀티노드 동작 (사내 테스트)
- [ ] K8s에서 OOM/장애 복구 시나리오 통과
- [ ] OIDC + SAML + SCIM 모두 통과
- [ ] ArgoCD GitOps 사이클 동작
- [ ] Helm 차트로 깨끗한 클러스터에 30분 내 설치 가능

### 비-목표 풀림
- K8s 멀티 노드 백엔드 (옵션)
- 사내 Git 호스팅 필수 옵션
- GitOps 통합

### 리스크 (윤곽)
- K8s 도입이 *동일 코드를 깨거나 운영 폭증* — SandboxBackend 인터페이스가 M0~M1부터 견고했는지가 관건
- 엔터프라이즈 기능이 *솔로 P1 UX를 무겁게* — 활성화 토글 필수
- 컴플라이언스 요구가 *예측 불가* — 사례 기반 점진

---

## M5 — 자동 운영 + 비즈니스 모델 (옵션)

### 진입 조건
- M4 완료 + 외부 사용자 누적 (수십 명+)
- 비즈니스 모델 결정 (코어 OSS 유지 vs 클라우드 라인 추가)

### 주제
P4 (자동 운영) 일급 + 지속 가능성.

### 작업 카테고리
1. **M5-E1: 헤드리스 자동화 1급**
   - Cron 트리거 UI (`/api/sessions/scheduled`)
   - Slack/Discord 트리거 → 세션 → 회신
   - GitHub Issue → 세션 (양방향 webhook)
   - 정책 엔진 확장: "auto-PR if green CI + small change" 같은 룰
2. **M5-E2: 에이전트끼리 협업**
   - 한 프로젝트 안 멀티 에이전트 (planner + executor + reviewer)
   - geny-executor의 SubPipelineFactory + cross-session 통신 (executor에 PR)
3. **M5-E3: 모델 비용 최적화**
   - 모델 라우터 (단순 작업 → Haiku, 복잡 → Opus) — *executor의 Stage 6 router strategy*에 일반화 PR
   - 캐시 적극 활용 (Stage 5)
   - 사용자별 50% 비용 절감 목표
4. **M5-E4: 클라우드 옵션 (비즈니스 결정 시)**
   - "GAPT Cloud" (자체 호스팅 SaaS)
   - 사용자 선택: 셀프호스트 vs 우리 클라우드
   - 코어 OSS 영구 유지
5. **M5-E5: 마켓플레이스 / 카탈로그 (옵션)**
   - 프로젝트 템플릿
   - MCP 서버 큐레이션 카탈로그 (보안 검토 거친 서버만)

### DoD (M5 안정)
- [ ] 외부 사용자 베이스 (수십 명+) 6개월 안정 운영
- [ ] 자동화 시나리오 (cron/webhook → 자동 PR) 사용자 N명이 매일 사용
- [ ] 모델 라우터로 사용자 측 *50% 비용 절감* 입증

### 비-목표 풀림 / 신규 도입
- 자동 운영 (P4) 일급
- (옵션) 클라우드 라인
- (옵션) 마켓플레이스

### 리스크 (윤곽)
- 비즈니스 모델이 *코어 OSS 정신과 충돌* — 코어 영구 OSS 보장, 클라우드는 옵션
- 마켓플레이스 보안 책임 — 큐레이션 부담 큼, 자동 다운로드 금지
- 자동 운영이 *통제 어려운 비용 폭주* 원인 — PolicyEngine + cost cap이 견고해야

---

## 윤곽 단계의 업데이트 규칙

- *위 단계 종료 시점에* 해당 단계의 윤곽을 *디테일한 epic 카드들*로 분해 (M1 패턴).
- 새 분해는 `docs/plan/m{n}/` 폴더에 들어간다.
- 본 outline 파일에서 분해된 단계는 *제거*하고, *그 다음 단계*만 윤곽으로 남긴다.
- M1 종료 → 본 파일은 M3~M5만 남고, `m2/` 폴더에 디테일이 생긴다.

---

## 비-목표 풀림 시점 (재게재)

| 비-목표 ([00](../00_overview.md) §0.4) | 풀리는 단계 |
|---|---|
| 신규 앱 생성 (Lovable/v0) | 영구 비-목표 |
| 로컬 IDE 확장 | 영구 비-목표 |
| 자체 모델 학습 | 영구 비-목표 |
| K8s / 멀티 노드 | M4 |
| 사내 Git 호스팅 (Forgejo) | M3 옵션, M4 필수 옵션 |
| 자체 모델 카탈로그 | M5 (옵션, 비즈니스 결정 시) |
| 데스크탑 앱 | 영구 비-목표 |
