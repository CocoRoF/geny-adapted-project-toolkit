# 12. Geny 첫 어댑트 케이스 스터디 (Geny Adaptation Case Study)

> **상위**: [00](00_overview.md) ~ [11](11_roadmap.md)
> **마무리 문서**

이 문서는 GAPT를 *어떻게* Geny에 적용할지 — M1의 두 번째 DoD 조건인 *"Geny 첫 어댑트 성공"* — 의 구체적 작업 절차를 케이스 스터디로 정리한다. 다른 외부 레포(예: blog, experiment-x) 어댑트의 *템플릿*으로도 쓰인다.

Geny는 *프로젝트 토대*가 복잡하다 — Compose 다중 파일, VTuber/Tamagotchi 도메인, geny-executor 2.1.0 통합, ~5개 manifest 템플릿(worker/vtuber/sub-worker 등), `EnvironmentService.instantiate_pipeline` 패턴, plan/progress/analysis cadence. 가장 가혹한 *어댑트 대상*이므로, 이 사례를 통과하면 GAPT는 일상 도구로 자리잡았다고 볼 수 있다.

**중요한 분리**: Geny와 GAPT는 *같은 상위 라이브러리(geny-executor)를 쓰는 별개 호스트*. GAPT가 Geny의 코드를 *import 하지 않는다*. GAPT가 *대상 레포로* Geny를 git clone해서 격리 컨테이너에서 운영. Geny의 VTuber/Tamagotchi/Persona 도메인은 GAPT와 무관.

---

## 12.1 Geny 컨텍스트 (현 상태 요약)

다음은 *작성 시점* 확인된 구조 (`/home/geny-workspace/Geny/`):

```
Geny/
├── README.md
├── docker-compose.yml
├── docker-compose.dev.yml
├── docker-compose.dev-core.yml
├── docker-compose.prod.yml
├── docker-compose.prod-core.yml
├── nginx/
├── backend/                    # FastAPI 백엔드, AgentSessionManager 포함
├── frontend/                   # 웹 프론트
├── omnivoice/                  # 음성
├── whisper-stt/                # STT
├── vendor/                     # 외부 (Live2D 등)
├── sample_audio/
├── img/
├── docs/                       # 기존 문서
├── progress/                   # cycle 기반 진행 기록 (v0.20.0 executor 통합)
├── plan.md
└── review.md
```

핵심 사실:
- **AgentSessionManager** (`backend/service/executor/agent_session_manager.py`, 1451 lines): VTuber 페르소나 / Tamagotchi 플러그인 / TickEngine / Persona Provider 등 활발한 *Geny 도메인 로직*. GAPT는 이 파일을 *건드리지 않음* (Geny의 자기 도메인).
- **`EnvironmentService.instantiate_pipeline(env_id, credentials, ...)` 패턴**: Geny가 이미 운영 중. GAPT의 `GaptEnvironmentService` ([04](04_llm_agent_layer.md) §4.7)가 같은 패턴.
- **v0.20.0 executor 통합 cycle 진행 중** (메모리 [[project_geny_plan_layout]]).
- **prod compose가 sudo로 돌고 $HOME=/root 함정** ([[feedback_sudo_compose_home_pitfall]]).
- **Geny 자체는 GAPT의 *대상 레포*** — Geny 안에 GAPT 통합 코드를 *넣지 않는다*. GAPT가 외부에서 Geny를 클론·관리.

---

## 12.2 어댑트 목표 한 줄

> *"Geny v0.20.0 executor 통합 cycle 하나(plan → implement → test → PR → deploy)를 GAPT 콘솔만으로 외부 IDE 없이 완수한다."*

성공의 측정:
- 데스크탑 Cursor를 *전혀 열지 않고* 한 사이클 완수.
- prod 배포까지 포함.
- 모든 액션이 GAPT audit에 기록.

---

## 12.3 어댑트 단계 (Step-by-Step)

### Step 1. Geny 프로젝트를 GAPT에 등록

```
1. https://toolkit.my-server.com 접속
2. "프로젝트 추가" → GitHub 레포 목록에서 Geny 선택
3. 자동 감지된 메타데이터 검토:
   - default branch: main (또는 v0.20.0 cycle 브랜치)
   - compose files: 4개 발견 (docker-compose.yml, dev.yml, dev-core.yml, prod.yml, prod-core.yml)
   - language: Python (backend) + TS/JS (frontend) + 기타
4. GAPT가 어떤 compose를 dev로 쓸지 묻는다 → 사용자: docker-compose.dev.yml
5. 환경 정의:
   - dev (LocalCompose, compose=dev.yml, secrets=.env.dev)
   - prod-vps (RemoteSSH, host=prod-vps.my-domain, compose=prod.yml, secrets=.env.prod)
6. 시크릿 등록 (Vault에 저장):
   - ANTHROPIC_API_KEY (사용자)
   - DATABASE_URL_DEV / DATABASE_URL_PROD
   - 기타 Geny의 .env.* 키들
```

이 단계의 결과: `Project(slug="geny")`, `Environment(dev)`, `Environment(prod-vps)`, `Secret(*)`가 DB에 생성. 아직 sandbox는 부팅 안 함.

### Step 2. 워크스페이스 생성 + sandbox 부팅

```
1. 좌측 트리에서 "geny" 클릭 → "워크스페이스 생성" → "main 브랜치, name='main'"
2. GAPT 백엔드:
   - 호스트에서 Sysbox 컨테이너 부팅 (gapt/runtime:latest)
   - 컨테이너 내부: git clone https://github.com/CocoRoF/Geny /workspace/main
   - GitHub OAuth Device Flow의 단명 토큰으로 인증 (askpass helper)
3. 컨테이너 내부: 사용자 compose 감지 → dev.yml로 docker compose up -d
4. Caddy에 subdomain 등록:
   - https://geny.preview.my-server.com → backend service
   - https://geny-frontend.preview.my-server.com → frontend service
5. 워크스페이스 진입. dockview 레이아웃 "Focus" 프리셋 (에디터 + 채팅).
```

리스크 1: **compose가 호스트 도커 소켓 마운트를 가정**한다면? — Sysbox 안의 inner dockerd가 *호스트와 격리된 dockerd* 소켓을 자기 안에 노출 → 사용자 패턴 동작. [06](06_isolation_and_runtime.md) 6.3.3 참조.

리스크 2: **VTuber/Live2D vendor가 큰 경우 clone이 길다** → `git clone --filter=blob:none` partial clone 옵션 사용자가 선택.

### Step 3. Geny의 cadence 컨텍스트 자동 로드

채팅 시작 전, 우측 컨텍스트 패널에 자동 표시:

- `README.md` (요약)
- `plan.md`, `progress/` (가장 최근 cycle)
- `CLAUDE.md` 또는 `.claude/AGENTS.md` (있다면)
- `docs/` 폴더 인덱스
- `backend/service/executor/` 디렉토리 트리
- `pyproject.toml`, `package.json`, compose 파일 요약

이 컨텍스트가 Stage 3 (System Prompt)에 *자동 주입* — Geny의 *작업 cadence*를 LLM이 알고 시작.

### Step 4. 첫 채팅 사이클 (plan)

사용자 입력:
> "v0.20.0 executor 통합 cycle의 다음 단계로, agent_session_manager의 idle monitor가 sandbox 재시작 후에도 살아남도록 수정하고 싶다. analysis/plan/progress 폴더에 cycle 정의부터 시작해줘."

LLM 응답 (Plan 모드):
1. `analysis/20260601_idle_monitor_resilience.md` 작성 (현 동작 + 문제 + 개선안 비교)
2. `plan/cycle_20260601_idle_monitor.md` 작성 (단계별 작업)
3. 코드 변경 대상: `agent_session_manager.py:_idle_tick_engine` 주변, TickEngine 복원 로직
4. 테스트 추가
5. PR 본문 작성
6. 사용자 리뷰 후 머지

사용자가 Plan 검토 → "1번은 좀 더 깊게, 2번은 OK, 그 외 진행" → "Act" 클릭.

### Step 5. 구현 (act)

에이전트가 다음을 데몬에 위임:
- `Read(backend/service/executor/agent_session_manager.py)` (관련 섹션만)
- `Read(backend/service/executor/agent_session.py)`
- `Read(backend/service/tick/__init__.py)`
- 분석 후 `Edit(analysis/20260601_*.md)` 새 파일 작성
- `Edit(plan/cycle_*.md)`
- `Edit(backend/service/executor/agent_session_manager.py)` 수정 (diff 카드)
- `Edit(backend/tests/test_idle_monitor.py)` 새 테스트
- `Bash(cd /workspace/main && pytest backend/tests/test_idle_monitor.py)` 실행
- 통과 → 다음, 실패 → 자동 디버그 시도

각 Edit 결과는 채팅에 diff 카드. 사용자가 *적용*을 명시.

### Step 6. inner loop 검증

- `Edit`가 적용되면 데몬 inotify가 변경 감지.
- `docker-compose.dev.yml`에 watch 정의가 있으면 자동 sync/restart.
- 없으면 사용자가 "Restart backend" 버튼 클릭.
- 프리뷰 iframe(`geny.preview.my-server.com`)에서 라이브 동작 확인.

### Step 7. 커밋 + PR + CI

사용자: "테스트도 다 그린이니 PR 올려줘"

에이전트:
- `Bash(git status)` → 변경 파일 목록
- `Bash(git add -A && git commit -m "...")` — 메시지는 LLM 작성, [[reference_git_identity]] Co-Authored-By 자동
- `Bash(git push -u origin cycle/idle-monitor-resilience)`
- `Bash(gh pr create --title ... --body-file pr_body.md --base main)`
- PR URL 표시

이후:
- GitHub Actions 자동 실행.
- GAPT가 polling으로 진행 표시 (CI 탭).
- 그린 → 사용자가 GitHub 웹에서 머지 (또는 GAPT에서 `gh pr merge` 도구로).

### Step 8. prod 배포

사용자: "prod-vps에 배포"

GAPT:
1. PolicyEngine 평가 → 이 프로젝트의 prod 환경은 기본 정책(2FA 필요 + LLM 직접 실행 deny) → TOTP 코드 요구. (사용자가 야간 자동 배포 등을 활성화한 경우엔 그 config가 우선)
2. SecretBackend에서 `.env.prod` 단명 조회.
3. RemoteSshTarget이 실행:
   - `ssh user@prod-vps.my-domain 'cd /apps/geny && git pull origin main && docker compose -f docker-compose.prod.yml -f docker-compose.prod-core.yml pull && up -d'`
   - **중요**: [[feedback_sudo_compose_home_pitfall]]에 따라 sudo로 돌면 HOME이 /root임을 인지. compose 파일이 `${HOME}/...` bind mount를 *기대하지 않도록* 사전 검증. (작업이 명시적으로 그것을 가정하지 않는 한.)
4. 진행 로그 라이브 스트림.
5. health check (사용자가 환경에 정의한 endpoint).
6. Slack 알림 (옵션).

### Step 9. Cycle progress 기록

에이전트가 마지막으로:
- `Edit(progress/cycle_20260601_idle_monitor.md)` — 사이클 종료 기록 (어떤 PR, 어떤 변경, 어떤 검증).
- 사용자 검토 후 커밋.

→ Geny의 *cadence가 자체적으로 유지* — GAPT가 cadence를 *촉진하지 방해하지 않음*.

---

## 12.4 Geny 어댑트에서 *반드시 보장되어야* 하는 GAPT 기능

이 케이스 스터디가 *통과 가능*하려면 GAPT의 M1이 다음을 모두 가져야 한다:

| # | 기능 | 본 문서 출처 | 관련 docs |
|---|---|---|---|
| C1 | GitHub OAuth Device Flow + 단명 git credential | Step 1, 2 | [05](05_git_workflow.md) §5.2 |
| C2 | Sysbox 컨테이너 + 사용자 compose 그대로 부팅 | Step 2 | [06](06_isolation_and_runtime.md) §6.3 |
| C3 | Caddy on-demand TLS + subdomain 자동 | Step 2 | [07](07_cicd_and_preview.md) §7.7 |
| C4 | 프로젝트 컨텍스트 자동 로드 (CLAUDE.md / plan.md / progress/) | Step 3 | [04](04_llm_agent_layer.md) §4.4 |
| C5 | Plan/Act 모드 UI + diff 카드 | Step 4~5 | [08](08_web_ide_ux.md) §8.4 |
| C6 | Read/Edit/Bash 도구 컨테이너 데몬 위임 | Step 5 | [04](04_llm_agent_layer.md) §4.5 |
| C7 | Inner loop: 파일 변경 → compose 재기동 | Step 6 | [07](07_cicd_and_preview.md) §7.2 |
| C8 | git commit/push/PR 자동 + Co-Authored-By | Step 7 | [05](05_git_workflow.md) §5.5 |
| C9 | GitHub Actions polling + 로그 라이브 스트림 | Step 7 | [07](07_cicd_and_preview.md) §7.3 |
| C10 | RemoteSSH deploy + 2FA + 시크릿 단명 주입 | Step 8 | [07](07_cicd_and_preview.md) §7.4, [09](09_security_authz_observability.md) §9.3 |
| C11 | Audit 이벤트 (모든 step) | 전체 | [09](09_security_authz_observability.md) §9.4 |
| C12 | 비용 라이브 표시 (이번 cycle의 LLM 비용) | 전체 | [04](04_llm_agent_layer.md) §4.9 |

C1~C12를 동시에 제공하면 *Geny 어댑트 통과*. 빠지면 어디서 막히는지 명확함.

---

## 12.5 Geny 특수성에서 만나는 함정과 대응

### 함정 G-1. 다중 Compose 파일

Geny는 `dev.yml`, `dev-core.yml`, `prod.yml`, `prod-core.yml` 4개를 *조합*해서 사용한다. GAPT는:
- 프로젝트 메타에 *조합 패턴* 저장:
  ```yaml
  compose_profiles:
    dev: [docker-compose.dev.yml, docker-compose.dev-core.yml]
    prod: [docker-compose.prod.yml, docker-compose.prod-core.yml]
  ```
- 데몬은 `docker compose -f f1.yml -f f2.yml up -d` 패턴 실행.

### 함정 G-2. 비교적 큰 의존성 (vendor/, sample_audio/, img/)

clone 시간 길고 디스크 큼. GAPT:
- 워크스페이스 생성 시 `--filter=blob:none` 옵션 제안.
- 디스크 한계 가까워지면 *오래된 sandbox volume* GC 안내.

### 함정 G-3. sudo HOME 함정 ([[feedback_sudo_compose_home_pitfall]])

prod에서 compose가 sudo로 돌면 `$HOME=/root`. GAPT는:
- *GAPT 관리 영속 파일은 SeaweedFS Mount*, 사용자 compose 내부 자체 named volume은 inner dockerd가 관리. `${HOME}` 패턴은 *경고 + 절대 경로 대안 제안*.
- 사용자 compose에 `${HOME}/...` 패턴 발견 시 *경고 + 대안 제안*.
- prod 환경 deploy 직전 검증.

### 함정 G-4. VTuber/Tamagotchi 도메인 복잡성

LLM이 도메인을 *모르고* 변경할 위험. GAPT:
- Stage 3 시스템 prompt에 `CLAUDE.md` + 핵심 디렉토리 indexes 자동 주입.
- 사용자가 *처음 채팅*에 도메인 요약을 직접 한 번 줘서 *프로젝트 메모리*에 저장 (S18 메모리).
- 이후 세션이 그 메모리를 자동 참조.

### 함정 G-5. agent_session_manager는 *상태가 많음* (1451 lines)

LLM이 한 번에 너무 많은 컨텍스트 — 비용 폭주 위험. GAPT:
- `Read` 도구가 *섹션 단위*로 가져옴 (line range 명시).
- Stage 5 Cache로 같은 영역 반복 조회 비용 절감.
- 사용자가 *targeted edit*만 시키도록 Plan에서 범위 좁히기.

### 함정 G-6. geny-executor 의존 — *3중 의존*

(a) **GAPT 컨트롤 플레인**이 geny-executor 2.1.0+에 의존, (b) **GAPT의 sandbox 안 toolkit-agent 데몬**도 geny-executor에 의존(컨테이너 안에서 별도 인스턴스 spawn 가능), (c) **대상 레포 Geny의 백엔드**도 geny-executor에 의존.

같은 버전을 써야 하는가? **격리되어 있어 *달라도 됨*.** 단:
- GAPT 컨트롤 플레인은 *항상 최신 안정 버전*에 의존.
- 컨테이너 안 sandbox는 *Geny가 사용 중인 버전*을 그대로 (compose 빌드가 의존성 잠금).
- *Geny의 다음 버전을 작업 중*인 cycle이면 GAPT 자체도 그 버전을 가능한 빨리 받아 도그푸드 ([[feedback_geny_executor_publish_workflow]]).

### 함정 G-7. claude_code_cli의 `--bare` / OAuth 함정

[[reference_geny_executor_v2_1]] + `docs/claude_code_cli.md` 참고. `bare_mode=True`는 OAuth 구독 path에서 자동 strip(2.0.6+)되지만, 사용자가 *직접 `extras["extra_args"]`에 `--bare` 넣으면* 그게 우선. GAPT는:
- `extras` 설정을 manifest/credential builder가 *한곳에서*만 결정.
- 사용자가 직접 `extra_args` 추가 시 *경고 모달*.

### 함정 G-8. CLI built-ins vs MCP wrap 도구 중복

`docs/claude_code_cli.md`: CLI built-in(Bash/Read/Write/Edit/Glob/Grep/WebFetch)이 MCP wrap과 동시 활성. 사용자가 *CLI built-in `Bash`로 git*을 하면 GAPT의 `mcp__gapt__gapt_git` 게이트(audit/PolicyEngine 재평가)를 우회.

→ Geny 어댑트 시 `settings_path`의 `permissions.allow`에서 `Bash`를 **카테고리 제한** (`"Bash(git status)"`, `"Bash(pytest *)"` 등)으로 좁히고, *임의 `Bash`*는 명시 거부. GAPT 도구를 통과해야 audit/policy가 일관됨.

---

## 12.6 어댑트 성공의 신호

다음이 모두 *자연스럽게* 일어나면 어댑트 성공:

- [ ] 사용자가 5분 안에 첫 채팅 메시지 보냄.
- [ ] cycle 1개를 *데스크탑 Cursor 0번 사용*으로 완수.
- [ ] prod 배포 성공.
- [ ] 비용 < 사용자 일일 cap.
- [ ] 모든 도구 호출이 audit에 보임, 의외 호출 없음.
- [ ] *사용자가 다음 cycle도 GAPT에서 하고 싶다*고 말함.

---

## 12.7 다른 레포 어댑트 템플릿

이 케이스에서 추출한 일반 템플릿 — 다른 외부 레포에도 동일 적용:

### Template Step 1: 프로젝트 등록
- Git 호스트 인증 (Device Flow)
- 자동 감지: 언어, compose 파일, package manager, .env 패턴
- 환경 정의 (적어도 dev, 가능하면 staging/prod)
- 시크릿 등록

### Template Step 2: 워크스페이스 부팅
- main 브랜치 1개로 시작
- compose 자동 부팅
- subdomain 노출

### Template Step 3: 컨텍스트 등록
- 자동 컨텍스트 (README, CLAUDE.md, plan/progress 있다면)
- 사용자가 *프로젝트 메모* 1회 입력 (선택, 강력 권장)

### Template Step 4~9: 일상 사이클
- Plan/Act, Edit, Bash, Commit, PR, CI, Deploy

이 템플릿이 *모든 레포에 동작*하는 것이 GAPT의 가치.

---

## 12.8 Geny ↔ GAPT 양방향 시너지

GAPT와 Geny는 *별개 코드베이스*지만, 다음 영역에서 *간접 시너지*:

| 영역 | Geny → GAPT | GAPT → Geny |
|---|---|---|
| geny-executor 발전 | GAPT의 요구가 executor PR으로 → Geny에도 흘러감 | GAPT가 executor를 빠르게 stress-test |
| MCP 서버 카탈로그 | (필요 시 공유) | GAPT 사용자가 발견한 좋은 서버를 추천 |
| docs/cadence 패턴 | Geny에서 검증된 plan/progress | GAPT가 같은 패턴 사용 |
| 컨테이너 운영 함정 | Geny에서 학습 ([[feedback_sudo_compose_home_pitfall]] 등) | GAPT가 자동 검증 / 경고 |

별개로 진화하되 *학습은 공유*.

---

## 12.9 본 케이스 스터디의 *문서 cadence*

이 문서가 *마지막 분석 문서*이지만 GAPT 작업은 이제 *시작*이다. 이후 다음 폴더 cadence:

```
geny-adapted-project-toolkit/
├── docs/                # 본 분석 (12편)
├── analysis/            # 새 주제 (예: m1_sandbox_poc.md)
├── plan/                # 진행 중 cycle 계획
├── progress/            # 완료된 cycle
├── src/                 # 코드 (M1부터)
├── poc/                 # M0의 PoC들
└── ...
```

각 cycle 시작 시 → analysis 작성 → plan 작성 → 코드/문서 변경 → progress 갱신 (PR 단위). [[feedback_durable_instructions]] 따름.

---

## 12.10 마무리

12편의 분석 문서로 GAPT의 *Phase 0 docs-first*가 일단락된다. 이후 M0 PoC → M1 tracer bullet → ...로 진행하면서, 본 docs는 *살아 있는 참조*로 갱신한다.

특히 본 12 문서(Geny case study)는 *현실의 어댑트가 보여줄 surpise*를 받아 자주 갱신될 가능성이 높다. 다른 모든 docs도 *불변식이 깨지면 즉시 수정*하는 cadence.

GAPT가 자기 자신을 GAPT에서 유지보수하고, Geny를 GAPT에서 운영하기 시작하는 순간이 *Phase 0의 진짜 완료*다.

---

## 부록 A — 본 docs 패밀리 색인 (재게재)

| # | 제목 | 한 줄 |
|---|---|---|
| 00 | [개요](00_overview.md) | 비전, 포지셔닝, 핵심 가치, 비-목표, 용어집 |
| 01 | [시장 풍경](01_market_landscape.md) | 12+ 경쟁 제품, 빈자리, 차용/회피 패턴 |
| 02 | [유스케이스/페르소나](02_use_cases_and_personas.md) | P1~P4, 5개 골든패스, 비-시나리오 |
| 03 | [시스템 아키텍처](03_system_architecture.md) | 컨트롤/실행 플레인, 8 도메인, 데이터 흐름 |
| 04 | [LLM 에이전트 레이어](04_llm_agent_layer.md) | geny-executor 재사용, 멀티 세션, 도구 권한 |
| 05 | [Git 워크플로](05_git_workflow.md) | clone/worktree/PR/credential |
| 06 | [격리/런타임](06_isolation_and_runtime.md) | Sysbox, compose, 리소스, 네트워크 |
| 07 | [CI/CD/프리뷰](07_cicd_and_preview.md) | inner/outer loop, deploy 어댑터, Caddy |
| 08 | [Web IDE UX](08_web_ide_ux.md) | Monaco+dockview, 채팅 1급, 단축키 |
| 09 | [보안/권한/감사/관측](09_security_authz_observability.md) | RBAC, Vault, Audit, OTel |
| 10 | [기술 스택 결정](10_tech_stack_decisions.md) | 결정 매트릭스, 라이선스 함정 |
| 11 | [로드맵](11_roadmap.md) | M0~M5 단계, 비-목표 해제 시점 |
| 12 | [Geny 케이스 스터디](12_geny_case_study.md) | (본 문서) |

---

## 부록 B — 사용자 검토 요청 사항

본 docs 12편은 *사용자 검토* 이후에 다음 단계(M0 PoC)로 넘어간다. 검토 시 특히 확인할 것:

1. **포지셔닝 한 줄 ([00](00_overview.md))** — *내 의도와 일치하는가*
2. **격리 1번 원칙 ([06](06_isolation_and_runtime.md))** — *호스트 docker 소켓 비-노출* 정신
3. **geny-executor 직접 의존 ([04](04_llm_agent_layer.md), [10](10_tech_stack_decisions.md))** — *fork 대신*
4. **LLM 권한 ⊆ 사용자 권한 ([09](09_security_authz_observability.md))** — *prod 배포는 사람만*
5. **비-목표 ([00](00_overview.md) §0.4, [11](11_roadmap.md) §11.8)** — *진짜 거기까지 안 가도 되는가*
6. **Geny 첫 어댑트 ([12](12_geny_case_study.md))** — *내 실제 사용 패턴과 맞는가*

검토 후 추가/수정 요청 → 본 docs 갱신 → M0 PoC 진입.
