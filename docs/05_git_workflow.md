# 05. Git 워크플로 (Git Workflow)

> **상위**: [03](03_system_architecture.md) / [04](04_llm_agent_layer.md)
> **다음**: [06_isolation_and_runtime.md](06_isolation_and_runtime.md)

이 문서는 GAPT의 **Git 통합 레이어**(`D3 Git Service`)를 정의한다. 외부 Git 호스트(GitHub/GitLab/Gitea) 연결, 인증, clone/worktree/PR 생성, 다중 브랜치 동시 작업, 인증 누출 방지를 다룬다.

핵심 결정 7개:

1. **`git` CLI subprocess가 1차** — 라이브러리(go-git/isomorphic-git/pygit2)는 호환성 함정 회피.
2. **워크트리 1급 시민** — 한 레포 다중 브랜치 동시 작업의 표준 메커니즘.
3. **OAuth Device Flow가 사용자 1차 인증 경로** — 폰/태블릿 친화, PAT보다 안전.
4. **PAT은 보조 + GitHub App은 M3+ 옵션**.
5. **단명 credential injection** — 토큰을 컨테이너 환경변수에 박지 않는다.
6. **셀프호스트 Git 서버(Forgejo)는 M3+ 옵션, 부품으로 두고 코어에 강제하지 않음**.
7. **fork 머지/리뷰는 외부 호스트(GitHub) PR 인터페이스에 위임** — UI에서 미러링만.

---

## 5.1 왜 `git` CLI subprocess인가

라이브러리 vs CLI 비교(Agent B 리서치 결과):

| 라이브러리 | 언어 | worktree | 부분 clone | LFS | 누수 사례 |
|---|---|---|---|---|---|
| libgit2/pygit2 | C/Py | 가능 | 부분 | 별도 | 빌드 의존성 무거움, libgit2 LFS 미지원 |
| go-git | Go | 부분 (CRUD 제약) | 부분 | 별도 | sparse-checkout 가장자리 케이스 누수 |
| isomorphic-git | JS | 부분 | 부분 | ❌ | 브라우저 한정 가치, 서버에선 약함 |
| simple-git | Node | 전부 (CLI wrapper) | 전부 | 전부 | 결국 CLI를 호출 |
| **`git` CLI** | — | 전부 | 전부 | 전부 | **호환성 100%** |

**결정**: 컨테이너 안에서 `git` 패키지를 그대로 설치(Alpine/Ubuntu 모두 표준). Python 측은 `asyncio.create_subprocess_exec`로 호출, stdout/stderr 캡처.

라이브러리 추상화는 *누수가 보장된 영역*(LFS, submodule, sparse-checkout, partial-clone, custom hooks). 우리는 그 누수를 피한다.

→ Git 명령 실행은 **데몬이 한다** (컨테이너 내부). 컨트롤 플레인은 *명령을 만들고 결과를 받기만* 한다 — 인증 정보가 호스트 FS에 남지 않게.

---

## 5.2 인증 전략

### 5.2.1 사용자 ↔ Git 호스트

| 방법 | UX | 보안 | 만료 | 적합도 |
|---|---|---|---|---|
| **OAuth Device Flow** | 폰/태블릿 친화 | 토큰 *서버에만* | 사용자 회수까지 | **1차 (M0~)** |
| **PAT (fine-grained)** | 사용자가 직접 발급/붙여넣기 | 권한 과대 위험 | 사용자 설정 | 2차 (Device Flow 안 되는 경우) |
| **GitHub App** | 일회 install | 가장 세분, 짧은 토큰 | 1시간 자동 회전 | M3+ 옵션 |
| **SSH 키** | git push 친숙 | API 별도 필요 | 사용자 회수 | M3+ 추가 |

**Device Flow 흐름**:
```
1. 사용자가 UI에서 "GitHub 연결" 클릭
2. 백엔드가 GitHub Device Flow 시작 → user_code + verification_uri 반환
3. UI: "github.com/login/device 열고 ABCD-1234 입력하세요"
4. 사용자가 폰/다른 브라우저로 진행
5. 백엔드 polling → access_token 획득
6. Secret Vault에 저장 (scope=user)
```

토큰의 평문은 *Vault에만*. DB의 `Project.git_remote.auth_ref`는 SecretRef 1개.

### 5.2.2 컨테이너 ↔ Git 호스트 (단명 credential injection)

**원칙**: 토큰을 컨테이너의 환경변수나 `.git-credentials` 파일에 *정적으로* 박지 않는다. 매 git 명령 직전에 단명 주입, 직후 폐기.

구현:
```bash
# 컨트롤 플레인이 데몬에 명령:
GIT_ASKPASS=/usr/local/bin/gapt-askpass.sh \
  git -c credential.helper="!gapt-askpass.sh" \
  push origin feat/x
```

`gapt-askpass.sh`는 데몬이 컨트롤 플레인의 unix socket에서 *1회 호출* token을 받아 stdout에 출력. 토큰은 *5초 ttl*. 사용 후 폐기.

이 패턴의 이점:
- 컨테이너 내 사용자(또는 LLM)가 `cat ~/.git-credentials`로 토큰을 훔칠 수 없다.
- 토큰이 컨테이너 이미지 layer/snapshot에 남지 않는다.
- 데몬이 *어떤 git 명령*에 토큰을 줬는지 audit.

---

## 5.3 워크트리 1급 시민

### 5.3.1 왜 워크트리인가

P1 사용자가 한 프로젝트에서 동시에 두 브랜치 작업하고 싶다는 시나리오(G3, [02](02_use_cases_and_personas.md))를 *단순 clone 두 번*으로 해결하면:

- 디스크 낭비 (대형 레포에서 수 GB ×2)
- fetch가 두 번
- LFS 캐시 분리
- git 설정 분리

**해결: `git worktree`** — 하나의 `.git` 디렉토리 + N개의 작업 디렉토리.

```
/workspace/
  .git/                  # 단일 git 디렉토리 (객체 저장소)
  main/                  # 워크트리 1: main 브랜치
    src/...
    docker-compose.yml
  feat-avatar/           # 워크트리 2: feat/avatar-integration 브랜치
    src/...
    docker-compose.yml
  agent-tmp/             # 워크트리 3: LLM 임시 실험용
    ...
```

### 5.3.2 워크트리 모델 ↔ GAPT 데이터 모델 매핑

```
Project (1) ─── (N) Workspace
                       │
                       ├─ worktree_path: "/workspace/main"
                       ├─ branch: "main"
                       ├─ compose_project_name: "geny-main"  # 포트 충돌 회피
                       ├─ sandbox_id: "..."  # 워크트리 ↔ sandbox 1:1 (M0~M2)
                       └─ session_ids: [...]
```

선택 사항:
- **워크트리 ↔ sandbox 1:1** (M0): 단순. 각 워크트리가 자기 컨테이너 + dockerd.
- **워크트리 N : sandbox 1** (M3+): 한 sandbox 안에 N개 워크트리 마운트. 메모리 절약. 단 compose stack 격리는 별도 처리.

M0~M2는 1:1로 시작.

### 5.3.3 컴포즈 stack 격리

같은 프로젝트의 두 워크트리가 *같은 포트*를 점유하려 하면 충돌. 해결:

- Compose `-p <project_name>`을 워크스페이스별로 다르게 (`-p geny-main`, `-p geny-feat-avatar`).
- Compose 안 `ports:` 매핑을 *동적으로 재작성*: 호스트 포트는 GAPT가 할당 (e.g., 8001/8002/...), 컨테이너 내부 포트는 그대로.
- 또는 *호스트 포트 노출 안 함* + Caddy reverse proxy로 subdomain 라우팅 (→ [07](07_cicd_and_preview.md)).

권장: subdomain 라우팅. 호스트 포트 노출은 *필요한 경우*만.

### 5.3.4 워크트리 라이프사이클

```
[생성]   사용자가 "새 브랜치에서 작업" 클릭
       → POST /api/projects/{pid}/workspaces { branch: "feat/x", from: "main" }
       → 데몬: git worktree add /workspace/feat-x -b feat/x main
       → Workspace 행 INSERT, sandbox 부팅, compose stack 시작

[전환]   사용자가 같은 프로젝트 좌측에서 워크스페이스 클릭
       → 백엔드가 해당 sandbox 헬스 체크, 필요시 깨움

[삭제]   사용자가 워크스페이스 삭제
       → 머지 안 된 변경 확인 (있으면 경고)
       → compose down, sandbox 정지/삭제
       → git worktree remove /workspace/feat-x
       → 브랜치 삭제는 *사용자 선택*
```

머지 안 된 변경의 안전망: `git status` + `git stash --include-untracked` 자동 백업 → 사용자에게 30일 보관 후 삭제 안내.

---

## 5.4 외부 Git 호스트 어댑터

### 5.4.1 인터페이스 (재게재)

```python
class GitProvider(Protocol):
    async def list_user_repos(self, auth) -> list[RemoteRepo]: ...
    async def clone(self, remote, target, auth) -> RepoRef: ...
    async def fetch(self, repo, auth) -> FetchResult: ...
    async def push(self, repo, branch, auth) -> PushResult: ...
    async def open_pr(self, repo, branch, title, body, auth) -> PRRef: ...
    async def get_pr_status(self, pr_ref, auth) -> PRStatus: ...
    async def list_workflow_runs(self, repo, branch, auth) -> list[CIRun]: ...
    async def get_workflow_run_logs(self, run_id, auth) -> AsyncIterator[str]: ...
```

### 5.4.2 구현체

| Provider | M0 | M3+ |
|---|---|---|
| **GithubProvider** | ✅ `gh` CLI + REST API | + GitHub App, webhooks |
| **GitlabProvider** | ✅ `glab` CLI + REST | + Self-Managed GitLab |
| **GiteaProvider** | (개발) | ✅ Forgejo 호환 |
| **GenericGit** | ✅ git CLI만 (PR 없음, push만) | — |

### 5.4.3 `gh` CLI에 의존하는 이유

GitHub Actions 결과, PR 생성, 이슈, 릴리스 — 모두 `gh` CLI가 가장 잘 추상화한다. REST API를 직접 호출하기보다:

```python
# 데몬에서:
gh pr create --title "{title}" --body-file body.md --base main --head feat/x
gh pr checks {pr_number} --watch
gh run view {run_id} --log
```

`gh` 출력은 `--json` 플래그로 구조화 — 파싱 안정적.

단점: 컨테이너 이미지에 `gh`를 미리 깔아야 함 (~25MB). 수용 가능.

### 5.4.4 webhook vs polling

| 방식 | 장점 | 단점 |
|---|---|---|
| Webhook (`workflow_run` 등) | 실시간, 무료 quota 절약 | 외부에서 우리 백엔드 도달 가능해야 |
| Polling (`gh run list`) | 외부 도달성 불필요 | rate limit 소모, 5~30s 지연 |
| **하이브리드** | 셋업 가능하면 webhook, 아니면 polling | — |

**M0**: 폴링 (사용자가 외부 도달 노출하지 않아도 됨). 사용자가 webhook을 활성화하면 자동 전환.

---

## 5.5 PR 자동화 플로우

GAPT의 가장 강한 차별점 중 하나. 사용자가 채팅에서 "PR 올려줘" → 다음이 자동:

```
1. (있다면) 변경 사항 git diff 요약 → 커밋 메시지 생성 (LLM)
2. git add -A; git commit -m "{generated}"
3. git push -u origin {branch}
4. gh pr create --title {generated_title} --body-file {generated_body.md}
   --base main --head {branch}
   (옵션) --label, --assignee, --milestone — 프로젝트 메타 설정 따라
5. 결과 PR URL을 채팅에 표시 + 좌측 패널 "Open PRs"에 등록
6. gh pr checks --watch 백그라운드 시작 → 결과를 라이브 스트림
```

### 커밋 메시지 / PR 본문 생성 정책

- *프로젝트 메타데이터*에 커밋 메시지 컨벤션 정의 가능:
  - `convention: "conventional"` (Conventional Commits)
  - `convention: "geny"` (Geny 스타일: cycle 번호 + 한국어 한 줄)
  - `convention: "custom"` (사용자 정의 prompt)
- 시그너처 자동 추가: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` ([[reference_git_identity]] 참조)
- PR 본문: 기본 템플릿 = Summary + Test plan. 프로젝트별 `.gapt/pr_template.md` 우선.

### 자동 머지 (옵션)

- *기본 OFF*. 사용자가 명시적 토글.
- CI 그린 + 사용자 리뷰 통과 시에만.
- `main`/`master` 또는 protected 브랜치는 자동 머지 *영구 거부* (Project setting으로 강제).

---

## 5.6 리뷰 / diff 표시

PR 리뷰는 외부(GitHub) 인터페이스를 *대체하지 않는다* — 미러링/요약 정도.

- PR 페이지에서 GAPT가 변경 사항 diff 표시 (Monaco diff 뷰어).
- "AI 리뷰 요청" 버튼 → 별도 세션이 *리뷰 preset*으로 시작 → 코멘트 초안 → 사용자 승인 → `gh pr review --body-file` 게시.
- 인라인 코멘트도 LLM이 생성 가능 (위치 정보는 diff hunk에서 추출).

---

## 5.7 fetch / sync 정책

워크스페이스 부팅 시:
- `git fetch --all --prune` 실행 (기본).
- 사용자가 *최근 fetch 30초 이내면 skip*.
- `main` 또는 사용자 지정 기본 브랜치가 *원격에서 앞서가면* 자동 알림.

리베이스/머지는 사용자 의도 명시 — 자동 안 함. *원격 브랜치에 강제 push는 금지* (force-with-lease만 허용, 그것도 명시적 확인).

---

## 5.8 LFS / 대형 파일

- Git LFS는 git CLI가 표준 지원 (`git lfs install`).
- 컨테이너 이미지에 `git-lfs` 패키지 포함.
- LFS 객체 저장은 외부 호스트(GitHub LFS) 그대로 사용.
- 우리 호스트의 LFS 캐시는 워크스페이스별 분리. 정기 GC.

대형 파일이 워크스페이스 부팅 시간을 지연 → 사용자 설정으로 *partial clone*(`--filter=blob:none`) 옵션.

---

## 5.9 submodule

- 모든 submodule 자동 init/update는 *기본 OFF* (예상 못한 외부 통신).
- 사용자가 명시적으로 활성화 → `git submodule update --init --recursive`.
- submodule도 같은 워크스페이스 안. 별도 워크스페이스로 분리하지 않음.

---

## 5.10 Forgejo 임베드 (M3+)

언젠가는 사용자가 *완전한 셀프호스트* (외부 GitHub 없이)를 원할 수 있다. M3+ 옵션:

- Compose에 `forgejo` 서비스 추가.
- GAPT의 `GiteaProvider` 어댑터를 Forgejo에 연결.
- 외부 GitHub 레포를 Forgejo에 *미러* 또는 *마이그레이션*.
- CI는 Forgejo Actions(runner 별도) 또는 Woodpecker.

**중요**: Forgejo는 GPLv3+ 카피레프트. *임베드*하지 않고 *옵셔널 외부 서비스*로 두면 라이선스 전파 없음. compose 파일에 image 참조만 두는 형태.

---

## 5.11 git 보안 함정 체크리스트

| 함정 | 우리의 방어 |
|---|---|
| 토큰을 `.git-credentials`에 정적 저장 | 단명 askpass helper로 대체 |
| 토큰을 LLM 응답에 노출 | 컨테이너 로그/응답에 시크릿 마스킹(정규식) |
| force push로 동료 작업 덮어쓰기 | `force-with-lease`만 허용, prod 브랜치는 거부 |
| 의도치 않은 브랜치/태그 삭제 | `git push --delete` 명시적 확인 |
| `.env`, `id_rsa` 등 시크릿 파일 commit | `.gitignore` 검증 + pre-commit hook 자동 설치 |
| LLM이 `git config --global` 변경 | sandbox `git config --local`만 허용 정책 |
| submodule URL 변조로 RCE | submodule 변경 사용자 명시 확인 |
| Repo가 hooks 디렉토리에 악성 코드 | `core.hooksPath`를 무력화 또는 review 게이트 |

---

## 5.12 후속 문서로 흘려보내는 책임

- **컨테이너 안에서 git이 안전하게 실행되는 격리** → [06](06_isolation_and_runtime.md)
- **PR 생성 후 CI 트리거 / 결과 표시** → [07](07_cicd_and_preview.md)
- **사용자 PAT/OAuth 토큰 보관** → [09](09_security_authz_observability.md)의 Secret Vault
- **OAuth Device Flow의 UI 흐름** → [08](08_web_ide_ux.md)
- **Forgejo 옵션의 라이선스 / 결정 매트릭스** → [10](10_tech_stack_decisions.md)

---

## 5.13 본 문서가 보장하는 인터페이스

1. **호스트 FS에 사용자 git 토큰의 평문이 남지 않는다** (Vault만).
2. **컨테이너 환경변수/파일에도 정적으로 박지 않는다** (단명 askpass).
3. **워크스페이스 ↔ 워크트리 1:1** (M0~M2).
4. **외부 git 호스트는 어댑터 인터페이스 뒤에** — 호스트 종속 코드 직접 의존 금지.
5. **force-push에는 force-with-lease + protected 브랜치 거부**.
6. **PR 생성 시 자동 commit 시그너처는 [[reference_git_identity]] 따름**.
7. **fetch/push/PR 모두 audit 이벤트 발행**.

이 보장들 위에서 [06](06_isolation_and_runtime.md)이 *컨테이너 격리*를 정의한다.
