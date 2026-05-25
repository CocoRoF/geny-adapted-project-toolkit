# M2 — Serving Capability (편집기 + Agent → 실제 서비스 운영)

> Status: planned
> Estimated: 3~4 작업주 / 6~9 PR (Phase A 만; Phase B/C 는 별도)
> Depends on: M1.5 close (`m1_5_dogfood_readiness.md`)
> Blocks: M2-E1 (멀티 프로젝트 동시 운영) 이후 epic 전부
> Relates to: [`../07_cicd_and_preview.md`](../07_cicd_and_preview.md),
> [`../06_isolation_and_runtime.md`](../06_isolation_and_runtime.md),
> [`m2_m5_outline.md`](m2_m5_outline.md) (재정렬)

## 0. 왜 이 카드를 새로 쓰는가

원래 [`m2_m5_outline.md`](m2_m5_outline.md) 의 M2 는 E1 멀티 프로젝트 →
E2 워크트리 → E3 프리뷰 → E4 UX 다듬기 순서로 짜여 있었다.
M1.5 close + 2026-05-25 audit 결과, 그 순서는 *틀렸다*:

> "이 프로젝트는 결국 다른 프로젝트를 서빙 + CI/CD + Agent 편집·테스트
> 결합 도구인데, 지금 다른 프로젝트를 제대로 서빙하는 기능이 전혀
> 없는 것으로 보여."
>
> — 사용자, 2026-05-25 (Chat 패널 dogfood 후)

사용자 진단은 정확하다. M1 까지 backend API 엔드포인트는 다 깔렸지만
*사용자가 클론한 프로젝트를 실제로 띄워서 보는 inner-loop* 가
operationally 빈 채. M2-E0 (또는 "Phase A — Inner-Loop Serve") 가
다른 epic 들보다 먼저 와야 한다.

## 1. 원래 목적 재확인 (한 줄)

> **"다른 프로젝트 서빙" + "CI/CD" + "Agent 편집·테스트" 결합 도구.**

세 다리가 함께 서야 한다:

1. **다른 프로젝트 서빙** — 클론한 repo 의 dev 서버를 IDE 안에서 띄우고
   브라우저로 접속 가능
2. **CI/CD** — push → CI 자동 → 그린이면 deploy 트리거 → 결과 가시화
3. **Agent 편집·테스트** — 채팅으로 코드 변경 + 테스트 실행 + 결과 확인

현재 (3)은 M1.5 까지 작동, (1)·(2)의 *backend* 는 있으나 *UI/통합* 부재.

## 2. 현재 상태 (2026-05-25 audit)

| Surface | Backend | UI | 평가 |
|---|---|---|---|
| **Deploy (LocalCompose / SSH / Webhook)** | ✓ 동작 (`DeployOrchestrator` 실 infra fire) | ✗ 버튼 없음 (curl-only) | API complete, UI 0% |
| **Preview (Caddy admin + share link)** | ✓ `CaddyAdminClient` 실 등록 | ▢ 수동 URL 입력만 | 자동 발견 부재 |
| **CI (GitHub Actions polling)** | ✓ `gh run list` 파싱 | ▢ 읽기 전용 리스트 | 트리거/로그/webhook 없음 |
| **Terminal (xterm.js → sandbox PTY)** | ✗ 백엔드 없음 | ▢ 패널 shell 만 | **불가능** |
| **Service logs streaming** | ✗ 컨테이너 stdout 캡처 없음 | ✗ 패널 없음 | **불가능** |
| **Port exposure (inner :3000 → outside)** | ✗ 매핑 메커니즘 없음 | ✗ 없음 | **불가능** |
| **Watch mode (compose override 자동)** | ✗ 사용자 수동 | ✗ 없음 | **불가능** |
| **Sandbox (Sysbox runtime)** | ✓ prod compose 정의 / dev 는 Mock | — | dev 에선 mock 호스트 실행 |
| **Agent + chat + tool call 시각화** | ✓ (M1.5) | ✓ | 작동 |

**한 줄 진단**: 사용자가 IDE 에서 클론한 Next.js repo 의 `npm run dev` 를
*돌릴 수도 없고*, *로그를 볼 수도 없고*, *브라우저로 접속할 수도 없다.*
Deploy 버튼도 없다. CI 도 그냥 보기만 가능.

## 3. 갭 카탈로그 (해결 우선순위)

기능 → 막힘 → 막힘 해소 cycle 매핑:

| # | 사용자가 못 하는 것 | 막힘 원인 | 해소 cycle |
|---|---|---|---|
| 1 | `npm start` 같은 명령을 sandbox 안에서 직접 실행 | 터미널 PTY/WebSocket 백엔드 부재 | **M2-A1** |
| 2 | dev 서버가 띄워졌는지 / 어떤 로그 찍는지 확인 | 컨테이너 stdout 스트림 엔드포인트 부재 | **M2-A1** |
| 3 | sandbox 안 `localhost:3000` 을 브라우저로 접속 | 포트 노출 → 호스트 → Caddy 자동 라우팅 X | **M2-A2** |
| 4 | dev 서버를 background 로 띄우고 코드 수정하면 자동 reload | watch 모드 / compose override 자동 패치 X | **M2-A2** |
| 5 | "Deploy" 버튼 한 번으로 staging/prod 에 배포 | env 관리 + deploy trigger UI 부재 | **M2-A3** |
| 6 | deploy 진행 / 실패 로그 라이브로 확인 | deploy stream 엔드포인트 미연결 | **M2-A3** |
| 7 | CI 실행 재시도 + 로그 / artifact 확인 | `gh run rerun` + 로그 페치 미구현 | **M2-A4** |
| 8 | PR 머지 → 자동 deploy (GitHub Webhook ingress) | Webhook 라우터 미구현 | **M3** (defer 유지) |
| 9 | preview 공유 링크 클릭 한 번으로 카피 + 외부 친구 보기 | share link UX 부재 | **M2-A2** sub |
| 10 | service health 가 ready 가 됐는지 indicator | health check / readiness 폴링 부재 | **M2-A2** sub |

## 4. M2 Phase A — Inner-Loop Serve (재정렬된 우선 순서)

원래 M2-E1 (멀티 프로젝트) 보다 *먼저* 이 Phase A 를 끝내야
"GAPT 로 다른 프로젝트를 실제로 돌릴 수 있다" 가 가능해진다.

### Cycle M2-A1 — Terminal + Service Logs (2 PR, ~3일)

**목표**: 사용자가 sandbox 안에서 임의 명령을 실행하고 stdout 을 라이브로
본다.

- **Backend**:
  - `SandboxBackend.exec_in` 확장 — long-running 프로세스를 attach 가능한
    `ExecHandle` (stdout/stderr async iterator + cancel)
  - 신규 `GET /api/workspaces/{wid}/terminal` (WebSocket) — bidirectional
    PTY (입력 + 출력). 인증은 기존 magic-link 쿠키.
  - 신규 `GET /api/workspaces/{wid}/services/{label}/logs` (SSE) — 백그라운드
    실행 중인 서비스의 stdout/stderr tail (compose 서비스 이름 또는
    GAPT-managed 프로세스 라벨)
  - Mock backend: 호스트 PTY 로 `bash` spawn (dev). Sysbox backend:
    `docker exec -it` PTY.

- **Frontend**:
  - 기존 `TerminalPanel` placeholder → `xterm.js` + `xterm-addon-fit` 연결
  - 신규 `LogsPanel` — 서비스 선택 드롭다운 + 라이브 tail + "맨 끝으로
    스크롤" 버튼
  - Layout 기본값에 Terminal 패널 추가 (Focus 프리셋에는 안 넣고, Debug
    프리셋에 넣음)

- **DoD**:
  - 사용자가 워크스페이스 IDE 에서 터미널 패널 열어 `ls` / `pwd` / `npm i`
    실행, 출력 라이브로 표시
  - `npm run dev` 같은 long-running 명령을 띄우고 5분 동안 자동
    keep-alive
  - Agent 가 채팅에서 도구로 호출하는 명령과는 *별개의 surface* — agent
    가 화면 차지 안 함

### Cycle M2-A2 — Port Exposure + Caddy Auto-Bind + Watch (2~3 PR, ~4일)

**목표**: dev 서버가 띄워진 inner-loop 포트를 자동 발견 → 외부 URL 부여
→ 변경 시 자동 reload.

- **Backend**:
  - `compose.override.gapt.yml` 자동 생성/패치 — 워크스페이스 생성 시
    bind mount + watch sync 추가
  - 신규 `POST /api/workspaces/{wid}/services/{label}/expose` — inner host:port
    → Caddy subdomain 등록. body: `{port, label?}`
  - 신규 `GET /api/workspaces/{wid}/services` — compose ps 파싱 (state +
    bound ports + health)
  - Polling readiness probe — 처음 `POST expose` 후 health check loop
    (max 60s) 끝나면 SSE 로 "ready" 알림
  - `ShareLinkIssuer` 결과를 frontend 가 "copy to clipboard" 가능하게
    payload 정리

- **Frontend**:
  - `ServicesPanel` (신규) — compose service 목록 + 상태 dot + 노출 버튼
  - PreviewPanel 개선 — expose 후 자동 URL 채워짐, 공유 링크 발행 버튼
    + 만료 표시
  - Sticky toast: "preview 가 준비됐어요 (12s)"

- **DoD**:
  - 사용자가 `npm run dev` 띄움 → Services 패널에 `web · ready` 표시 →
    "expose" 클릭 → `https://<ws>.preview.<domain>/` 가 즉시 열림
  - 코드 수정 후 저장 → watch 가 인식 → 1~2초 안에 reload 반영
  - share link 발행 → 클립보드 복사 → 외부 친구 브라우저로 접속 가능

### Cycle M2-A3 — Deploy UI (env 관리 + trigger + 진행 stream) (1~2 PR, ~3일)

**목표**: Settings or 별도 surface 에서 환경 관리 + 한 클릭 deploy.

- **Backend**:
  - 신규 `GET/POST/PUT/DELETE /api/projects/{pid}/environments` —
    EnvironmentManifest 의 control plane
  - 신규 `GET /api/environments/{eid}/deploys/{run_id}/stream` (SSE) —
    deploy 진행 로그 실시간
  - `DeployOrchestrator` 가 진행 로그를 stream 으로 emit (현재는 결과만
    반환)

- **Frontend**:
  - 신규 route `/projects/:pid/environments` — env 카드 (이름, target,
    last deploy, status)
  - `DeployModal` (M1-E4 4.2 plan 에 있었음) — version 입력, target
    options 폼, 2FA 입력, "Deploy" 버튼
  - Deploy progress 패널 — SSE 로 라이브 로그 + spinner + 성공/실패
    뱃지 + rollback 버튼

- **DoD**:
  - 사용자가 staging 환경 등록 (LocalCompose target, dev compose 파일)
  - "Deploy v0.1" 클릭 → policy 통과 → 진행 로그 라이브 → 성공 시
    "https://staging.<domain>" 링크 표시
  - 실패 시 1-클릭 rollback

### Cycle M2-A4 — CI 트리거 + 로그 (1 PR, ~2일)

**목표**: GitHub Actions 의 결과만 보는 게 아니라 재실행 + 로그까지.

- **Backend**:
  - `GithubProvider` 에 `rerun_workflow(run_id)`, `fetch_logs(run_id, job_id)`
  - 신규 `POST /api/projects/{pid}/ci/runs/{run_id}/rerun`
  - 신규 `GET /api/projects/{pid}/ci/runs/{run_id}/logs` (텍스트 stream)

- **Frontend**:
  - CiPanel 의 각 run 에 "Re-run" 버튼 + "Logs" 토글 (인라인 expandable)
  - 실패한 job 의 로그를 ChatPanel "이거 왜 실패했지?" 로 자동 첨부
    가능 (선택 사항 — defer)

- **DoD**:
  - 실패한 CI run 의 로그를 IDE 안에서 확인
  - 재실행 한 클릭으로 트리거

### Cycle M2-A5 — Audit + Retrospective (commit X, plan/progress 마감)

- M2-A1~A4 cycle 통합 retrospective
- M2-E1 (멀티 프로젝트) 카드 디테일화 — 본 사이클이 끝났을 때의 학습
  반영

## 5. 비-목표 (M2-A 에서 *안* 함)

- 워크트리 다중 sandbox 동시 실행 — M2-E2 본 cycle 로 미룸
- 와일드카드 도메인 자동 DNS — M2-E3 본 cycle. M2-A2 는 사용자가
  미리 `*.preview.<domain>` DNS 를 설정해놨다고 가정
- GitHub Webhook ingress (push → 자동 CI/CD) — M3-E5 유지
- Cron / scheduled deploy — M5
- Kubernetes target — M4
- Forgejo 임베드 — M3-E5 옵션
- LSP 1차 — M3-E7

## 6. DoD (M2 Phase A 종료 게이트)

다음 시나리오를 사용자가 끝까지 통과해야 M2-E1 으로 넘어간다:

1. **Run**: 클론한 Next.js repo → 터미널에서 `npm install && npm run dev`
   → 로그 패널에서 "ready - started server on 0.0.0.0:3000" 확인
2. **Expose**: Services 패널에서 `web` 의 expose 클릭 → 자동 발급된
   `https://demo.preview.<domain>/` 가 새 탭에서 열림 (외부 친구도 접속
   가능)
3. **Watch**: `app/page.tsx` 1줄 수정 + Ctrl+S → 브라우저가 자동 reload
4. **Agent**: 채팅에 "이 페이지에 헤더 추가해줘" → agent 가 파일 수정 →
   diff 패널에 변경 표시 → 사용자 approve → 브라우저 reload 로 결과 확인
5. **Deploy**: Settings 에서 staging env 등록 → DeployModal 에서 "Deploy
   HEAD" → 진행 로그 라이브 → 성공 → staging URL 클릭으로 접속
6. **CI**: GitHub Actions failed run 의 로그를 IDE 안에서 읽음 → 재실행
   → 성공

## 7. 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| PTY WebSocket 이 Cloudflare 터널 통과 시 끊김 | M2-A1 통과 불가 | 처음부터 `cloudflared` 가 WebSocket 지원 — 단 long-polling fallback 도 준비 |
| Sysbox dev 환경 부재 (Mock 만) → host PTY = 보안 구멍 | M2-A1 dev 한정 | Mock 백엔드는 *호스트 worktree 안에서만* PTY spawn, 외부 경로 reject |
| Caddy admin API 가 production 에서 비활성화 (`admin off`) | M2-A2 fail | install.md 에 admin endpoint enable 가이드 + 헬스 체크 |
| `docker compose watch` 가 사용자 compose 파일 형식 변경 강제 | M2-A2 friction | 자동 override `.gapt-watch.yml` 생성 (원본 비건드) |
| 사용자 GitHub PAT 가 `workflow` scope 없음 → re-run 거부 | M2-A4 fail | Settings 의 GitHub PAT 설명에 필요 scope 명시 |
| Deploy SSE 가 fastapi-uvicorn keepalive 와 충돌 | M2-A3 잦은 끊김 | M1.5 의 SSE keepalive 패턴 재사용 (`X-Accel-Buffering: no` + 15s keepalive 코멘트) |

## 8. 사용자 측 작업 (Phase A 통과 후)

- 본인 repo 1개를 처음부터 끝까지 ("run → edit → deploy") 통과해서
  영상 / 노트로 기록
- 친구에게 share link 한 번 보내서 외부 접근 검증

## 9. 관련 docs

- [`../00_overview.md`](../00_overview.md) — 원래 비전
- [`../07_cicd_and_preview.md`](../07_cicd_and_preview.md) — CI/CD + preview
  아키텍처
- [`../06_isolation_and_runtime.md`](../06_isolation_and_runtime.md) —
  Sysbox + inner dockerd 설계
- [`../12_geny_case_study.md`](../12_geny_case_study.md) — Geny 어댑트
  step 6~9 가 본 Phase A 와 직접 매핑
- [`m2_m5_outline.md`](m2_m5_outline.md) — *재정렬됨*. 본 카드가 M2 의
  실질적 첫 카드 (E1~E4 보다 우선)
- [`../progress/m1/_post_close_hardening.md`](../progress/m1/_post_close_hardening.md) +
  [`../progress/m1_5_dogfood_readiness.md`](../progress/m1_5_dogfood_readiness.md) —
  본 cycle 의 출발점이 된 hardening 들
- [[feedback_durable_instructions]] — cycle cadence
- [[feedback_extend_executor_not_adapter_layer]] — sandbox-side 도구는
  executor 일반화로 (Phase A2 의 watch 자동화는 GAPT 어댑터 OK — 인프라
  계층이므로 executor 와 무관)
