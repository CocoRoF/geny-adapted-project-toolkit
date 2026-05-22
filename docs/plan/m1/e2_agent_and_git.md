# M1-E2: 에이전트 세션 + Git 통합

> Status: planned
> Estimated: 10 작업일 / 10 PR
> Depends on: M1-E1
> Blocks: M1-E3, M1-E4
> Relates to: [`../../04_llm_agent_layer.md`](../../04_llm_agent_layer.md) (전체), [`../../05_git_workflow.md`](../../05_git_workflow.md), [[reference_geny_executor_v2_1]]

## 목적 (한 줄)
**`Pipeline.from_manifest_async`**로 프로젝트 컨텍스트에 묶인 에이전트 세션을 부팅하고, **CLI MCP wrap**으로 GAPT 도구(특히 `gapt_git`/`gapt_pr`)를 CLI의 LLM에 노출한 뒤, GitHub OAuth Device Flow로 인증된 토큰으로 워크스페이스 안에서 git clone/commit/push/PR 생성까지 흘러가게 한다.

## 진입 조건
- [ ] M0-P3 통과 (executor + MCP bridge PoC)
- [ ] M1-E1 통과 (백엔드 토대)
- [ ] [`04`](../../04_llm_agent_layer.md) §4.3 (`gapt_default.json`) 일독
- [ ] [`05`](../../05_git_workflow.md) §5.2 (인증), §5.5 (PR 자동화) 일독

## DoD
- [ ] `gapt_default.json` 프로덕션 manifest가 `gapt_server` 내부에 ship됨
- [ ] `POST /api/projects/{pid}/sessions` → AgentSession 생성 + SSE 스트리밍 시작
- [ ] CLI MCP wrap을 통해 `mcp__gapt__gapt_read` / `gapt_glob` / `gapt_grep` / `gapt_edit` 모두 동작 (4개 핵심 도구)
- [ ] GitHub OAuth Device Flow로 토큰 등록 → 워크스페이스 안 git clone이 *호스트 FS에 토큰 평문 안 남기고* 통과 (askpass helper)
- [ ] `gapt_git` 도구로 commit/push 가능
- [ ] `gapt_pr` 도구로 PR 생성 가능 (gh CLI 경유)
- [ ] PolicyEngine PRE_TOOL_USE 훅이 `mcp__gapt__*` 호출 시점에 발화 (MCP bridge 안에서 재평가)
- [ ] geny-executor의 `exec.*.*` 에러 코드가 audit + UI 응답에 그대로 노출
- [ ] 세션 비용/토큰이 라이브 (1초 디바운스) 카운터로 제공
- [ ] freshness 정책 (5분/30분/6시간/24시간) ARQ 백그라운드 작업

## 작업 항목 (세부)

### Cycle 2.1 — GaptEnvironmentService + manifest ship (1 PR)
- `gapt_server/agent/environment_service.py`:
  - `instantiate_pipeline(env_id, credentials, adhoc_providers, subagent_registry=None) -> Pipeline`
  - 내부에서 `EnvironmentManifest.load(...)` + `Pipeline.from_manifest_async(...)` 호출
  - `env_id` resolve 우선순위: project override → `.gapt/manifests/{id}.json` (워크스페이스 내) → server `manifests/{id}.json`
- 서버 번들 manifest:
  - `gapt_server/manifests/gapt_default.json` ([04](../../04_llm_agent_layer.md) §4.3 전체)
  - `gapt_server/manifests/gapt_planning.json` (Plan/Act 강화)
  - `gapt_server/manifests/gapt_review.json` (Read-heavy)
- `strict=True` 로드 + 부팅 시 sanity check (provider 단일 출처 등)

### Cycle 2.2 — CredentialBundle 빌더 (1 PR)
- `gapt_server/agent/credentials.py`:
  - `build_for_session(project, workspace, session, secret_vault, daemon_socket, bridge_token) -> CredentialBundle`
  - claude_code_cli provider:
    - `api_key`: project.anthropic_secret_ref가 있으면 거기서 단명 read, 없으면 빈값(OAuth)
    - `binary_path`: 컨테이너 내 `/usr/local/bin/claude`
    - `extras`: §4.5.2 구조 그대로
  - SDK provider도 manifest에서 선택 가능하도록 anthropic/openai/google/vllm 키도 *있으면* bundle에 포함
- 시크릿 read는 *Pipeline 부팅 직전*, 메모리에만, 부팅 후 즉시 zeroize
- 모든 read는 audit (`secret.read`, `purpose="agent_session"`)

### Cycle 2.3 — MCP stdio bridge 프로덕션화 (1 PR)
- M0-P3의 `poc/mcp_bridge/server.py`를 `runtime/src/gapt_runtime/mcp_bridge/server.py`로 승격
- 변경:
  - tools/list가 *컨테이너 데몬*에 의존 (호스트 RPC 아님)
  - 컨테이너 데몬이 *컨트롤 플레인*으로 RPC (mTLS unix socket)
  - PolicyEngine 평가 결과를 컨트롤 플레인이 반환 → bridge가 deny면 `tool_result.isError=true`
  - 응답 streaming (긴 결과)
- 환경변수: `GAPT_BRIDGE_TOKEN`, `GAPT_BRIDGE_DAEMON_SOCK`
- 부팅 위치: `extras["mcp_config"]`이 `command=python3 -m gapt_runtime.mcp_bridge.server`를 가리킴 — CLI가 spawn

### Cycle 2.4 — GaptToolProvider (Read/Glob/Grep/Edit) (1 PR)
- `gapt_server/agent/tools/` 4개 도구를 `AdhocToolProvider` 인터페이스로:
  - `gapt_read(path: str, line_offset?: int, limit?: int)` → 데몬에 위임
  - `gapt_glob(pattern: str)` → 데몬에서 `rg --files | rg <pattern>` 또는 `fd`
  - `gapt_grep(pattern: str, path?: str)` → 데몬에서 `rg`
  - `gapt_edit(path: str, old: str, new: str, all?: bool)` → 데몬에서 diff 적용
- 각 도구는 `Tool` ABC 구현:
  - `name`, `description`, `input_schema` (JSON Schema)
  - `execute(args, ctx) -> ToolResult` — 컨테이너 데몬 RPC
- Provider의 `resolve(name)`이 매핑

### Cycle 2.5 — GitHub OAuth Device Flow (1 PR)
- `gapt_server/domains/auth/github_oauth.py`:
  - `POST /api/integrations/github/connect` → `{user_code, verification_uri}` 반환
  - 백엔드가 GitHub Device Flow polling → access_token → Secret Vault 저장 (scope=user, `git_provider=github`)
  - `DELETE /api/integrations/github` → 토큰 폐기
- Project 등록 시 `git_auth_secret_ref` 자동 매핑

### Cycle 2.6 — GitProvider + GithubProvider (2 PR)
- `GitProvider` Protocol (03 §3.6):
  - `list_user_repos`, `clone`, `fetch`, `push`, `open_pr`, `get_pr_status`, `list_workflow_runs`, `get_workflow_run_logs`
- `GithubProvider` 구현:
  - `gh` CLI subprocess (컨테이너 데몬에서 실행, askpass helper로 토큰 주입)
  - REST API 보조 (`PyGithub` 또는 `httpx` 직접)
  - 단명 askpass: `/usr/local/bin/gapt-askpass.sh` (5초 ttl 단명 토큰을 환경변수로)
- 호스트에 토큰 평문 캐싱 X — 매 git 호출마다 데몬 ↔ 컨트롤 RPC로 1회용 발급

### Cycle 2.7 — `gapt_git` + `gapt_pr` 도구 (2 PR)
- `gapt_git(action: 'commit'|'push'|'status'|'log'|'diff'|'branch'|'checkout'|'add', args: dict)` → 데몬 + GitProvider
  - commit 자동 시그너처: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` ([[reference_git_identity]])
  - protected 브랜치 (`main`/`master`) push는 PolicyEngine `git.push.protected` → 기본 DENY
- `gapt_pr(action: 'create'|'review_request'|'merge', args: dict)`:
  - `create`: title/body 생성 후 `gh pr create`
  - `merge`: PolicyEngine `git.pr.merge` 통과 + protected 브랜치 merge는 추가 확인
  - `review_request`: 라벨/리뷰어 지정
- Audit에 `git.push` / `git.pr_create` / `git.pr_merge` 명시

### Cycle 2.8 — ProjectAwareSessionManager (2 PR)
- `gapt_server/agent/manager.py` ([04](../../04_llm_agent_layer.md) §4.7 구현):
  - `create_session(project, workspace, user, env_id) -> AgentSession`
  - 내부 흐름: sandbox ensure → bridge_token mint → CredentialBundle 빌드 → GaptToolProvider 생성 → EnvironmentService.instantiate_pipeline → HookRunner 부착 → EventBus 구독 → DB row → audit
  - `stream(session_id, user_input) -> AsyncIterator[PipelineEvent]` — SSE 어댑터에 위임
  - `interrupt(session_id)` → `pipeline.cancel()` + 데몬 PTY kill
  - `archive(session_id)` — manifest snapshot 저장 + sandbox 정리 신호
- ARQ 작업 — freshness policy:
  - 5분: 그대로
  - 30분: 사용자에게 SSE 알림
  - 6시간: sandbox pause
  - 24시간: archive + sandbox stop
- AgentSession 객체: pipeline + project/workspace/user 메타 + cost tracker

### Cycle 2.9 — HookRunner: Policy + Audit + Cost (1 PR)
- `gapt_server/agent/hooks/`:
  - `policy_hook.py` — PRE_TOOL_USE에서 PolicyEngine.evaluate 호출, deny 시 `ToolFailure(ACCESS_DENIED)`
  - `audit_hook.py` — PRE/POST 모두 AuditSink로
  - `cost_hook.py` — POST에서 Stage 7 (Token) 이벤트 보강 + 누계 (라이브 1초 디바운스)
- `EventBus` 구독:
  - `api.*` → audit + cost
  - `tool.*` → audit + cost (도구별 duration)
  - `stage.error` → audit + 사용자 알림
- `pipeline.attach_runtime(hook_runner=runner)` ([`docs/hooks.md`](../../11_roadmap.md))

### Cycle 2.10 — 세션 API + SSE (1 PR)
- `POST /api/projects/{pid}/sessions {workspace_id, env_id?, user_input?}` → session_id
- `POST /api/sessions/{sid}/invoke {message}` → 즉시 200, SSE로 토큰 스트림
- `GET /api/sessions/{sid}/stream` (SSE) — 이벤트 타입:
  - `event: text` — 토큰 청크
  - `event: tool_call` — 도구 호출 시작
  - `event: tool_result` — 도구 결과
  - `event: cost` — 누계 USD (디바운스)
  - `event: error` — `exec.*.*` 코드 포함
  - `event: done` — 완료
- `POST /api/sessions/{sid}/interrupt` → cancellation token
- `GET /api/sessions/{sid}/messages?since=...` — 리플레이
- 모든 응답 JSON에 `exec_code` 필드 (있는 경우)

## 산출물
```
server/src/gapt_server/
├── agent/
│   ├── environment_service.py
│   ├── manager.py
│   ├── session.py
│   ├── credentials.py
│   ├── tools/{provider.py, git_tool.py, pr_tool.py, read_tool.py, glob_tool.py, grep_tool.py, edit_tool.py}
│   ├── hooks/{policy_hook.py, audit_hook.py, cost_hook.py}
│   └── streaming.py
├── domains/git/{provider.py, github.py, askpass.py}
├── domains/auth/github_oauth.py
├── manifests/{gapt_default.json, gapt_planning.json, gapt_review.json}
└── routers/{sessions.py, integrations.py}

runtime/src/gapt_runtime/mcp_bridge/server.py

tests/
├── agent/
├── git/
├── manifests/test_strict_load.py
└── e2e/test_e2_smoke.py
```

## 검증 시나리오
1. 사용자 GitHub OAuth Device Flow 완료 → 토큰 Vault에 저장 → 평문은 어디에도 없음.
2. 외부 git 레포로 워크스페이스 생성 → `gapt_git({"action": "status"})` 결과가 채팅에 표시.
3. 채팅: "이 레포의 README를 한국어로 요약해줘" → `mcp__gapt__gapt_read({"path": "README.md"})` 호출 → CLI가 결과 받고 요약 응답.
4. 채팅: "이 파일 한 줄 고치고 commit 해줘" → `gapt_edit` 적용 → `gapt_git({"action": "commit", "message": "..."})` 통과.
5. 채팅: "PR 올려줘" → `gapt_pr({"action": "create", ...})` → 결과 PR URL이 SSE로 표시.
6. 채팅: "main에 force push 해줘" → PolicyEngine deny → CLI가 사용자에게 거부 메시지.
7. Anthropic 키 일부러 잘못 → `exec.cli.auth_failed` audit + SSE error.
8. `cost_budget_usd` 초과 → `exec.stage.guard_rejected` audit + SSE error + UI 모달.

## 리스크 + 대응
| 리스크 | 영향 | 대응 |
|---|---|---|
| askpass helper가 컨테이너 환경에서 PATH 인식 못 함 | 중 | `GIT_ASKPASS` 절대경로 + 매 호출 검증 |
| MCP bridge가 컨트롤 플레인 다운 시 무한 대기 | 중 | bridge timeout (10s) + retry 1회 + `exec.tool.transport` 반환 |
| `gh pr create`가 인터랙티브 프롬프트 요구 | 작음 | `--title`/`--body-file`/`--head`/`--base` 모두 명시, fallback `--fill` |
| 사용자가 잘못된 manifest로 환경 만들면 부팅 실패 | 작음 | `strict=True` + 에러 메시지 명확 + 마지막 성공 manifest로 fallback 옵션 |
| 토큰 회전 중 in-flight git 호출이 끊김 | 중 | 토큰 grace period 30초 + 호출 단위 atomic |
| `gh` CLI 버전이 컨테이너 안에서 호스트와 달라 출력 형식 차이 | 작음 | `gh --version` pin, `gapt/runtime` 이미지에 동봉 |
| Policy denied 메시지가 LLM에게 다시 들어가 무한 루프 | 중 | PolicyEngine deny 시 *동일 도구 같은 인자 N회 반복* 감지 + Stage 16 (Loop) controller가 종료 신호 |

## 관련 docs
- [`../../04_llm_agent_layer.md`](../../04_llm_agent_layer.md) §4.3 manifest, §4.4 GaptToolProvider, §4.5 MCP wrap, §4.6 PolicyEngine, §4.7 SessionManager
- [`../../05_git_workflow.md`](../../05_git_workflow.md) §5.2 인증, §5.4 GithubProvider, §5.5 PR 자동화
- [`../../09_security_authz_observability.md`](../../09_security_authz_observability.md) §9.2.3 PolicyEngine + §9.3 Secret
- [[reference_geny_executor_v2_1]]
