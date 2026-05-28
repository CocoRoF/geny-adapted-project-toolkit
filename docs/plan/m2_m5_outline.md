# v1 범위 outline — M2 Phase B-Hardening / C / D

> **상위**: [`00_master_plan.md`](00_master_plan.md)
>
> **v1 범위 확정 (2026-05-28)**: GAPT 는 **single-admin self-hosted** 도구.
> 멀티유저 / OIDC / RBAC / K8s 백엔드 / 엔터프라이즈 기능은 v1 에서
> 모두 제외. 추후 확장 가능성은 열어두지만 코드/UX 결정은 단일-admin
> 가정 위에서 진행 ([[feedback_gapt_single_admin_auth]]).

---

## 0. 현재까지 완료된 작업 요약

| Milestone | 상태 | 진척 비고 |
|---|---|---|
| M0 (PoC 문서) | ✅ | 12 분석 문서 + 부트스트랩 |
| M1 (Tracer bullet) | ✅ | 4 epic (E1~E4), Dogfood + Geny adapt |
| M1.5 (Dogfood readiness) | ✅ (3✓/3~) | 1.5-A~F. geny-executor stage-6 블로커는 상위 의존 |
| M2 Phase A (Serve capability) | ✅ (4✓/2~) | Terminal / Services / Deploy / CI / retrospective |
| M2 Phase B (Preview domain) | ✅ | Cloudflare provider, subdomain mode, wildcard cert 가이드, migration wizard, deploy UX 재구성 — 본 outline 의 원래 M2-E3 "프리뷰 노출 강화" 가 이 phase 에 모두 들어감 |

---

## M2 Phase B-Hardening — **다음 (선행 필수)**

Phase B 의 production-grade 인프라가 들어간 직후, 누적된 robustness 부채를 갚는 단계.

### 진입 조건
- Phase B 종료 (Cloudflare provider live, subdomain mode 사용자 검증 완료)
- 사용자가 직접 `https://<slug>.<preview-domain>` 1회 이상 외부에서 접근 확인

### 주제
"기능은 다 들어갔다 — 이제 죽지 않고 회귀 안 나는 상태로 만든다."

### 작업 카테고리
1. **B.H.1 — 서버 라이프사이클 강건화**
   - 현재 GAPT server 가 Claude shell 의 자식 process 로만 살아있어 task stop 시 502. 자체 supervisor 필요.
   - docker compose 안의 `server` 서비스로 통합 + `restart: unless-stopped`
   - `scripts/dev/server.sh` (start/stop/status/logs) — Claude shell 의존성 차단
   - Healthcheck + 자동 재시작 (30s 무응답 시)
   - `docs/operations/dev_setup.md` 가이드
2. **B.H.2 — Phase B 테스트 백필**
   - Cloudflare client / service / migration 단위 테스트 (httpx mock)
   - SubdomainManager host-only splice + slug-change cleanup 회귀 테스트
   - `_env_with_fallback` (stopped 존중 / success 부활) 시나리오
   - StackManager.logs() smoke
   - 목표: server 53 → 75+ 파일, web 21 → 30+ 파일, 신규 모듈 coverage 70%+
3. **B.H.3 — UX edge case 정리**
   - 스택 stop 후 미등록 subdomain → GAPT 리다이렉트 버그 (zone-wide 404 fallback)
   - Cloudflare API 토큰 vault corruption 재현 + 자동 복구 UI
   - Token scope 자기진단 (Tunnel:Edit / DNS:Edit / SSL:Edit 별 ping)
   - Subdomain 진단 next_steps 중복 제거
4. **B.H.4 — Migration 안전망**
   - Cutover 자동 backup (systemd unit + ingress JSON dump)
   - 자동 rollback: cutover 후 30s 안에 cloudflared healthy 안 되면 drop-in 제거 + restart
   - `provider_migrations` 테이블 + history UI + 1-click revert
   - Cutover dry-run 모드

### DoD (B-H → C 게이트)
- [ ] Claude 세션 / 노트북 절전 / 재부팅 어떤 경우에도 서버 자동 복귀 (10초 이내)
- [ ] `pnpm vitest && uv run pytest` 신규 코드 70% 이상 라인 커버
- [ ] 스택 stop 후 외부 URL 방문 시 404 (GAPT 리다이렉트 X)
- [ ] Migration history 표 + 1-click revert 동작

### 리스크
- docker compose 안으로 server 옮기면 host 의 Vite 와 통신 경로 변경. `host.docker.internal` 의존성 명시
- 테스트 백필 중 노출되는 잠복 버그 — 발견되면 fix 후 회귀 추가

---

## M2 Phase C — Multi-project Operations

원래 outline 의 M2-E1 / E2 / E6 (멀티 프로젝트 / 워크트리 / 빌드 캐시). single-user 가정이므로 권한 분리는 없음.

### 진입 조건
- Phase B-Hardening 완료
- 사용자가 동시에 2개 이상 프로젝트 다룰 실제 수요 시점

### 주제
하루 종일 GAPT 안에서 N개 프로젝트 + 여러 브랜치를 동시 운영.

### 작업 카테고리
1. **C.1 — Worktree-1st workspace 모델**
   - `Workspace.git_worktree_path` 필드 + `(project_id, branch)` unique
   - `git worktree add/remove` 자동 (workspace lifecycle 에 연동)
   - UI: 브랜치 chip + 다른 branch 클릭 → 새 workspace 자동 생성
   - 동시 N개 workspace = N개 컨테이너 (per-workspace `gapt-ws-<wid>` 모델 활용, [[feedback_gapt_two_containers_per_workspace]])
   - 정리: 가리키는 브랜치 삭제되면 workspace 자동 archive
2. **C.2 — 멀티 프로젝트 동시 운영 UX**
   - 좌측 트리 다중 프로젝트, `Ctrl+P` 빠른 전환
   - 프로젝트별 비용 분리 집계 + 통합 대시보드
   - 활성 sandbox 수 cap + UI 표시 (리소스 보호)
3. **C.3 — Build cache**
   - Docker BuildKit local cache mount (`/var/lib/docker/buildx-cache`)
   - DeployModal 에 "cache hit/miss/skipped" 표시
   - env 설정에 build-cache mode (auto/aggressive/off)
   - 목표: 동일 이미지 재배포 30%+ 단축
4. **C.4 — 모바일 PWA shell (read-only)**
   - PWA manifest + Service Worker
   - `/m/projects/:pid` mobile route — 사이드바 + chat read + deploy status
   - (옵션) Web push 알림 — deploy 성공/실패
   - 단일 admin PIN 입력 인증

### DoD (C → D 게이트)
- [ ] 한 프로젝트의 `main` + `feature/x` 두 workspace 동시 열어 chat/edit/deploy 독립 동작
- [ ] 4개 프로젝트 동시 활성 상태에서 호스트 자원 cap 초과 안 함
- [ ] 동일 이미지 재배포 시간 30%+ 단축 측정
- [ ] 모바일 Safari/Chrome 에서 PWA 설치 + chat read + deploy alert 동작

### 비-목표 풀림
- (멀티 워크트리 운영 — 본 단계)

### 리스크
- N개 workspace 동시 = 호스트 자원 폭증. cap UI + 사용자 알림 필수
- 모바일 UX 범위 폭발 — 보조 사용 한정 (edit/deploy 는 데스크탑)

---

## M2 Phase D — Agent UX 폴리시

원래 outline 의 M2-E4 (UX 다듬기). chat 워크플로 개선.

### 진입 조건
- Phase C 완료

### 주제
사용자가 매일 chat 으로 코드 변경 ↔ 적용하는 사이클의 마찰을 최소화.

### 작업 카테고리
1. **D.1 — Plan/Act mode 분리**
   - Plan: read-only tools 만 (`Read`, `Grep`, `WebSearch`), markdown 으로 계획 정리
   - Act: 사용자 승인 후 mutation tools (`Write`, `Edit`, `Bash`) 활성화
   - Chat header segmented 토글 + 현재 mode 표시
   - 명시적 선택 안 하면 기존 자유 chat (backward compat)
2. **D.2 — Diff 그루핑 + 부분 적용**
   - 한 chat turn 의 diff 를 file/intent 별 그룹
   - 그룹별 체크박스 — 일부만 commit
   - 그룹 헤더에 LLM-생성 한 줄 요약
   - "모두 Approve" 단축
3. **D.3 — Conversation 영속성 + resume**
   - Chat events 를 DB 에 영속 (SSE 끊김 후 token-by-token replay)
   - 세션 검색 (key 단어 → past chats)
4. **D.4 — 단축키 + 명령 팔레트 확장**
   - Cmd+P / Cmd+K 표준화
   - 자주 쓰는 액션 (deploy / open file / search chat / new workspace) 팔레트화
5. **D.5 — 레이아웃 프리셋**
   - dockview 4종 프리셋 안정화 (default / chat-focused / debug / minimal)
   - 사용자 정의 5번째 슬롯

### DoD (D → v1 종료 게이트)
- [ ] Plan/Act mode 토글로 위험 변경 차단 시나리오 통과
- [ ] Diff 부분 apply 로 "이 파일만 OK" 가능
- [ ] 채팅 UX 가 *Cursor 와 나쁘지 않음* 수준 (사용자 자체 평가)
- [ ] 일주일 자가 사용 + 외부 1명 친구가 GAPT 셋업 시도해서 막힌 점 정리

### 리스크
- Plan/Act 가 *기존 chat 빠른 흐름 방해* — 명시적 opt-in 토글 필수
- Diff 그루핑이 LLM-생성 요약에 의존 — fallback (file 별 단순 그룹) 필요

---

## v1 종료 — Phase D 후

Phase D 종료 시점에 v1 spec 완성. 이후의 발전 방향은 *사용자 본인의 실제 사용 데이터* 기반으로 재정의 (현재 시점에서 미리 짜는 것 = 낭비).

---

## 명시적 out-of-scope (v1 결정)

추후 확장 가능성은 열려있으나 v1 코드/UX 의사결정에 영향 주지 않음:

| 항목 | 이유 |
|---|---|
| 멀티유저 / Org / Membership 모델 | single-admin 결정 ([[feedback_gapt_single_admin_auth]]) |
| OIDC / SAML / SCIM | single-admin |
| RBAC 4계층 권한 | single-admin |
| 옵션 모듈 (Forgejo / Woodpecker / openvscode embed) | self-hosted 도구의 코어 단순성 유지 |
| K8s SandboxBackend | local docker compose 로 충분 |
| Helm 차트 + 컴플라이언스 (SOC 2 등) | enterprise 영역, v1 비-목표 |
| Cloud SaaS / 마켓플레이스 | 비즈니스 모델 결정 후, OSS 코어와 분리 |

v1 의 모든 신규 코드는 위 항목들을 **염두에 두지 않고** 작성. 추후 풀게 되면 별도 marathon 으로.

---

## 비-목표 풀림 (v1 한정 재게재)

| 비-목표 ([00](../00_overview.md) §0.4) | 풀리는 단계 |
|---|---|
| 신규 앱 생성 (Lovable/v0) | 영구 비-목표 |
| 로컬 IDE 확장 | 영구 비-목표 |
| 자체 모델 학습 | 영구 비-목표 |
| 멀티 프로젝트 동시 운영 | Phase C |
| 멀티 워크트리 | Phase C.1 |
| 외부 프리뷰 노출 (cloudflared 자동) | **Phase B 완료** |
| 빌드 캐시 최적화 | Phase C.3 |
| 모바일 PWA (read-only) | Phase C.4 |
| Plan/Act mode | Phase D.1 |
