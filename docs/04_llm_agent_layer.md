# 04. LLM 에이전트 레이어 (LLM Agent Layer)

> **상위**: [03_system_architecture.md](03_system_architecture.md)
> **다음**: [05_git_workflow.md](05_git_workflow.md)
> **기준**: geny-executor 2.1.0 (`docs/architecture.md`, `docs/providers.md`, `docs/claude_code_cli.md`, `docs/mcp.md`, `docs/hooks.md`, `docs/memory.md`, `docs/manifest.md`, `docs/error_codes.md` 일독 후 작성)

이 문서는 GAPT의 **두뇌**(`D5 Agent Session Manager`)를 정의한다. **핵심 메시지 하나**: *GAPT는 자체 에이전트를 만들지 않는다.* GAPT는 **geny-executor 2.1.0의 `Pipeline.from_manifest_async` 위에 얹힌 얇은 호스트**일 뿐이고, 1차 backend는 `claude_code_cli` provider + **host MCP wrap**으로 GAPT의 도구를 CLI의 LLM에 노출한다.

---

## 4.0 핵심 결정 7개

1. **Manifest = 단일 진실원.** GAPT는 `EnvironmentManifest` JSON 템플릿을 ship + 사용자/프로젝트별 인스턴스 → `Pipeline.from_manifest_async(...)`. 코드로 빌드 X. ([[reference_geny_executor_v2_1]])
2. **1차 provider = `claude_code_cli`** + **host MCP wrap**으로 GAPT 도구(git/compose/deploy/preview/pr) 노출. SDK provider(anthropic/openai/google/vllm)는 manifest 편집으로 선택 가능 — *추가 어댑터 다층화 금지* ([[feedback_extend_executor_not_adapter_layer]]).
3. **세션 단위 격리**: AgentSession ↔ Workspace ↔ Sandbox 컨테이너 (1:1:1).
4. **PolicyEngine = `HookRunner` 위의 정책 평가자** ([[feedback_policy_config_not_hardcode]]). `PRE_TOOL_USE` 훅에서 deny/허용/추가확인 결정 → `ToolFailure(code=ACCESS_DENIED)`로 veto.
5. **에러 표시/i18n = `exec.*.*` 안정 코드 그대로 사용.** 자체 에러 계층 추가 안 함.
6. **비용/감사 = Stage 7(Token) + Stage 10(Tool) 이벤트** → `EventBus` 구독 → D8 Audit.
7. **GAPT 만의 추가 추상화는 최소화**. Geny가 이미 검증한 `EnvironmentService.instantiate_pipeline(env_id, credentials, ...)` 패턴을 그대로 따른다.

---

## 4.1 왜 geny-executor 2.1.0을 그대로 쓰는가

### 4.1.1 이미 모든 것이 있다

`docs/providers.md` + `docs/claude_code_cli.md` + `docs/mcp.md` + `docs/manifest.md` + `docs/hooks.md` + `docs/memory.md`를 통해 확인된 *우리가 필요한 모든 능력*:

| GAPT가 원하는 것 | geny-executor 2.1.0이 제공하는 것 |
|---|---|
| 21단계 검사 가능한 에이전트 루프 | `Pipeline` + Stage 1~21 (`docs/architecture.md`) |
| 외부 manifest로 환경 재구성 | `EnvironmentManifest.load()` + `Pipeline.from_manifest_async()` |
| Claude Code CLI를 backend로 | `claude_code_cli` provider (`ClaudeCodeCLIClient`) |
| 호스트 도구를 LLM에 노출 | 2 boundary MCP — `MCPManager`(host-attached) + `extras["mcp_config"]`(CLI wrap) |
| 도구 dispatch 전 정책 평가 | `HookRunner` `PRE_TOOL_USE` |
| 토큰/비용 추적 | Stage 7 + `EventBus` token 이벤트 + `cost_budget_usd` |
| 런타임 정책/도구 변경 | `PipelineMutator.swap_strategy/update_config/...` |
| 안정 에러 식별자 | `ExecutorErrorCode` (`exec.*.*`) |
| 멤버리 read/write 분리 | Stage 2 strategies + Stage 18 strategies + `MemoryProvider` |
| Sub-agent orchestration | Stage 12 + `SubagentRegistry` |
| Human-in-the-loop | Stage 15 (`gated` / `timeout_based`) |

**우리는 새로 만들 게 없다.** GAPT 백엔드는 *Pipeline 인스턴스의 라이프사이클* 과 *세션 ↔ 프로젝트 컨텍스트 연결*만 책임진다.

### 4.1.2 의존 vs Fork vs 재작성 — 결정

| 선택지 | 결정 |
|---|---|
| **PyPI 직접 의존** (`geny-executor==2.1.0` 이상) | **★ 영구 원칙** |
| Fork | ❌ Geny와 능력 분기, 평행 진화 부담 |
| 자체 재작성 | ❌ 재발명, 검증 손실 |

**원칙**: 능력 확장이 필요하면 *상위 앱 어댑터로 우회하지 않고* geny-executor 본체를 일반화 업그레이드 → PR → publish ([[feedback_geny_executor_publish_workflow]]) → GAPT는 의존 버전만 올림.

### 4.1.3 Geny가 이미 같은 패턴을 운영 중

`docs/manifest.md` 마지막 절 "Where Geny uses this"에서 발췌:

> Geny는 ~5개 manifest 템플릿(worker / vtuber / sub-worker / …)을 ship하고, 운영자가 웹 UI로 복제·편집한다. 매 세션은 `EnvironmentService.instantiate_pipeline(env_id, credentials, ...)`로 manifest를 resolve. 그 함수가 `Pipeline.from_manifest_async`를 호출.

**GAPT는 같은 패턴을 따른다** — 차이는 (a) *템플릿 셋이 다름* (worker/vtuber 대신 gapt_default/planning/review/headless), (b) *adhoc_providers가 GAPT 도구* (git/compose/deploy), (c) *Sandbox 컨테이너 안 데몬에 도구를 위임하는 추가 레이어*. 핵심 메커니즘은 동일.

---

## 4.2 GAPT 에이전트 레이어 모듈 구조

```
src/gapt/agent/
├── __init__.py
├── manager.py              # ProjectAwareSessionManager
├── session.py              # AgentSession (Pipeline 인스턴스의 얇은 래퍼)
├── environment_service.py  # GAPT 버전 EnvironmentService — manifest resolve
├── manifests/              # 번들 manifest 템플릿 (JSON)
│   ├── gapt_default.json   # claude_code_cli + host MCP wrap + 기본 도구
│   ├── gapt_planning.json  # Stage 14 evaluate=criteria_based + plan mode
│   ├── gapt_review.json    # PR 리뷰 전용 (read-heavy, write 제한)
│   └── gapt_headless.json  # cron/webhook용 (HITL bypass, budget tight)
├── tools/                  # GaptToolProvider (AdhocToolProvider 구현)
│   ├── provider.py         # GaptToolProvider — manifest.tools.external 해소
│   ├── git_tool.py
│   ├── compose_tool.py
│   ├── deploy_tool.py
│   ├── preview_tool.py
│   └── pr_tool.py
├── mcp_bridge/             # Sandbox 컨테이너 안에서 도는 MCP stdio bridge
│   ├── server.py           # ~150 LoC stdio loop, Geny의 geny_mcp_bridge.py 패턴
│   └── transport.py        # 호스트 toolkit-agent 데몬으로 RPC
├── hooks/                  # PolicyEngine + audit/telemetry hooks
│   ├── policy_hook.py      # PRE_TOOL_USE → PolicyEngine.evaluate → veto or pass
│   ├── audit_hook.py       # PRE/POST → AuditEvent
│   └── cost_hook.py        # POST → cost tracker
├── credentials.py          # CredentialBundle 빌더 (Vault → bundle 변환)
├── lifecycle.py            # 세션 생성/스트리밍/인터럽트/종료
└── streaming.py            # PipelineEvent → SSE 변환
```

각 모듈의 책임은 좁다. **새 에이전트 로직을 GAPT 안에 짜지 않는다** — Stage/Strategy 확장이 필요하면 geny-executor에 PR.

---

## 4.3 Manifest 템플릿: `gapt_default.json`

GAPT의 *디폴트 진입점*. 다른 템플릿은 이걸 베이스로 일부 stage strategy/config만 override.

```json
{
  "name": "gapt-default",
  "metadata": {
    "owner": "gapt",
    "tags": ["gapt", "default"],
    "version": "1"
  },
  "stages": [
    { "order": 1, "name": "input", "active": true, "artifact": "default",
      "config": {"max_chars": 200000}, "strategies": {} },
    { "order": 2, "name": "context", "active": true, "artifact": "default",
      "config": {}, "strategies": {"loader": "progressive_disclosure"} },
    { "order": 3, "name": "system", "active": true, "artifact": "default",
      "config": {}, "strategies": {"builder": "composable"} },
    { "order": 4, "name": "guard", "active": true, "artifact": "default",
      "config": {}, "strategies": {"chain": ["token_budget", "cost", "iteration"]} },
    { "order": 5, "name": "cache", "active": true, "artifact": "default",
      "config": {}, "strategies": {"policy": "adaptive"} },
    { "order": 6, "name": "api", "active": true, "artifact": "default",
      "config": {
        "provider": "claude_code_cli",
        "model": "sonnet",
        "max_tokens": 16384
      },
      "strategies": {"retry": "exponential_backoff"} },
    { "order": 7, "name": "token", "active": true, "artifact": "default",
      "config": {}, "strategies": {"tracker": "detailed"} },
    { "order": 8, "name": "think", "active": true, "artifact": "default",
      "config": {}, "strategies": {"processor": "extract_and_store"} },
    { "order": 9, "name": "parse", "active": true, "artifact": "default",
      "config": {}, "strategies": {"parser": "default"} },
    { "order": 10, "name": "tool", "active": true, "artifact": "default",
      "config": {}, "strategies": {"executor": "sequential"} },
    { "order": 11, "name": "tool_review", "active": true, "artifact": "default",
      "config": {}, "strategies": {"reviewer": "passthrough"} },
    { "order": 12, "name": "agent", "active": true, "artifact": "default",
      "config": {}, "strategies": {"orchestrator": "single_agent"} },
    { "order": 13, "name": "task_registry", "active": true, "artifact": "default",
      "config": {}, "strategies": {"registry": "passthrough"} },
    { "order": 14, "name": "evaluate", "active": true, "artifact": "default",
      "config": {}, "strategies": {"evaluator": "signal_based"} },
    { "order": 15, "name": "hitl", "active": true, "artifact": "default",
      "config": {}, "strategies": {"gate": "passthrough"} },
    { "order": 16, "name": "loop", "active": true, "artifact": "default",
      "config": {}, "strategies": {"controller": "budget_aware"} },
    { "order": 17, "name": "emit", "active": true, "artifact": "default",
      "config": {}, "strategies": {"emitter": "streaming"} },
    { "order": 18, "name": "memory", "active": true, "artifact": "default",
      "config": {}, "strategies": {"writer": "vault"} },
    { "order": 19, "name": "summarize", "active": true, "artifact": "default",
      "config": {}, "strategies": {"summarizer": "llm_summary"} },
    { "order": 20, "name": "persist", "active": true, "artifact": "default",
      "config": {}, "strategies": {"persister": "sqlite"} },
    { "order": 21, "name": "yield", "active": true, "artifact": "default",
      "config": {}, "strategies": {"formatter": "streaming"} }
  ],
  "tools": {
    "built_in": [
      "Read", "Glob", "Grep", "TodoWrite",
      "memory_write", "memory_read", "memory_search", "memory_list"
    ],
    "external": [
      "gapt_git", "gapt_compose", "gapt_deploy",
      "gapt_preview", "gapt_pr"
    ],
    "mcp_servers": []
  },
  "max_iterations": 60,
  "cost_budget_usd": 1.0
}
```

**중요한 디테일**:
- `stages[6].config.provider = "claude_code_cli"`가 *유일한* provider 결정 위치 (strict-load).
- `tools.external`은 `GaptToolProvider`(아래 §4.4)가 해소.
- `tools.mcp_servers`는 *host-attached* MCP (Stage 10이 직접 dispatch). CLI wrap MCP는 *manifest가 아닌* per-session credentials의 `extras["mcp_config"]`에 들어감 (§4.5).
- `max_iterations` / `cost_budget_usd`는 manifest 한 곳에만 (single source). Stage 4 Guard가 강제.

다른 템플릿은 이걸 베이스로:
- `gapt_planning`: `stages[14].strategies.evaluator = "criteria_based"`, `stages[15].strategies.gate = "gated"`(Plan/Act 게이트).
- `gapt_review`: `stages[10]` 거의 무력화 + Read-only built-ins만, `stages[14].config = {"criteria": ["correctness", "tests", "style"]}`.
- `gapt_headless`: `stages[15].strategies.gate = "passthrough"`, `cost_budget_usd: 0.5`, `max_iterations: 30`.

---

## 4.4 GaptToolProvider — `AdhocToolProvider` 구현

`docs/manifest.md`의 `adhoc_providers`는 `tools.external` 이름을 `Tool` 인스턴스로 해소하는 sequence. GAPT는 하나의 `GaptToolProvider`를 그 sequence에 패스.

```python
# gapt/agent/tools/provider.py (의사코드)
from geny_executor.tools.base import Tool

class GaptToolProvider:
    """resolve(name) → Tool. Pipeline.from_manifest_async가 호출."""

    def __init__(self, sandbox_ref, daemon_client, git_service, deploy_orchestrator):
        self._registry = {
            "gapt_git":     GitTool(sandbox_ref, daemon_client, git_service),
            "gapt_compose": ComposeTool(sandbox_ref, daemon_client),
            "gapt_deploy":  DeployTool(deploy_orchestrator),  # PolicyEngine 게이트 적용
            "gapt_preview": PreviewTool(sandbox_ref),
            "gapt_pr":      PrTool(git_service),
        }

    def resolve(self, name: str) -> Tool | None:
        return self._registry.get(name)
```

각 `Tool`의 `execute()`는 **컨테이너 안 toolkit-agent 데몬으로 RPC**. 데몬은 inner dockerd 안에서 실제 명령 실행 → 결과 + 파일 변경 manifest 반환 → `ToolResult`로 감싸 리턴.

**중요**: `claude_code_cli` provider를 쓸 땐 *대부분의 도구가 CLI 내부에서 직접 호출*된다 (§4.5). GAPT 도구는 *MCP bridge를 통해* CLI에게 노출되며, dispatch는 CLI가 한다. Stage 10은 SDK provider 사용 시에만 의미 있는 dispatch를 한다 (CLI 사용 시 `tool_use`는 drop되므로 no-op).

---

## 4.5 MCP 2 boundary — GAPT가 양쪽 다 사용

`docs/mcp.md`가 명시하는 두 가지 *독립* 경계:

### 4.5.1 Host-attached MCP (`manifest.tools.mcp_servers[]`)

- 호스트(GAPT 백엔드)가 외부 MCP 서버를 spawn → `MCPManager`로 관리 → `ToolRegistry`에 등록.
- *모든* Stage 6 provider가 native하게 봄 (anthropic SDK 사용 시 `tool_use` 블록으로, claude_code_cli 사용 시 CLI의 LLM이 native MCP로).
- 예시: 사용자가 `filesystem` MCP를 manifest에 추가하면 어떤 provider를 쓰든 동일하게 동작.

manifest 패턴:
```json
"tools": {
  "mcp_servers": [
    {"name": "filesystem", "transport": "stdio", "command": "npx",
     "args": ["-y", "@anthropic/mcp-filesystem", "/workspace"], "env": {}}
  ]
}
```

### 4.5.2 CLI MCP wrap (`extras["mcp_config"]` — `claude_code_cli` 한정)

- *역방향*: GAPT가 spawn한 `claude` 프로세스에게 *GAPT 자신의 tool registry*를 노출.
- CLI의 LLM이 `mcp__<server>__<tool>` 형태로 GAPT 도구 호출 → CLI 내부에서 직접 dispatch (Stage 10 안 거침).
- `tool_use` 블록은 `APIResponse`에서 *drop* (2.0.6+, 재dispatch 방지).

**GAPT의 기본 셋업**:
```python
# gapt/agent/credentials.py (의사코드)
def build_credentials(project, workspace, session, secret_vault) -> CredentialBundle:
    bridge_token = mint_short_lived_token(session.id, ttl_s=session.timeout_s)
    daemon_socket = f"/run/gapt/sessions/{session.id}/agent.sock"

    mcp_config = {
        "mcpServers": {
            "gapt": {
                "type": "stdio",
                "command": "/usr/local/bin/python3",
                "args": ["-m", "gapt.agent.mcp_bridge.server"],
                "env": {
                    "GAPT_BRIDGE_SOCKET": daemon_socket,
                    "GAPT_BRIDGE_TOKEN": bridge_token,
                    "GAPT_PROJECT_ID": project.id,
                    "GAPT_WORKSPACE_ID": workspace.id,
                    "GAPT_SESSION_ID": session.id,
                },
            },
        },
    }

    anthropic_key = secret_vault.read(session.anthropic_secret_ref, audit_ctx=session.audit_ctx())

    return CredentialBundle(by_provider={
        "claude_code_cli": ProviderCredentials(
            api_key=anthropic_key,
            binary_path="/usr/local/bin/claude",
            extras={
                "bare_mode": True,                # OAuth path면 자동 strip
                "workspace_root": "/workspace",   # CLI subprocess cwd
                "default_permission_mode": "default",
                "settings_path": json.dumps({
                    "permissions": {
                        "allow": [
                            "mcp__gapt",        # GAPT 브릿지 전체
                            "Read", "Edit", "Glob", "Grep",
                            "Bash(git *)", "Bash(pytest *)", "Bash(npm *)",
                        ],
                    },
                }),
                "mcp_config": mcp_config,
                "max_budget_usd": project.cost_cap_per_session_usd,
                "timeout_s": 600.0,
            },
        ),
    })
```

이 셋업이 *GAPT의 기본 작동 모드*다. 다른 provider로 바꾸려면 manifest에서 `stages[6].config.provider`만 바꾸면 됨.

### 4.5.3 둘 다 쓰는 경우

GAPT는 *한 세션에서 둘 다* 사용 가능. 예: `filesystem` MCP 서버는 host-attached로 두고 (Stage 10이 dispatch), `gapt` MCP wrap은 CLI 내부에서 GAPT 도구 호출에. 충돌 없음.

---

## 4.6 PolicyEngine = `HookRunner` 위 정책 평가자

[[feedback_policy_config_not_hardcode]] + [09](09_security_authz_observability.md) §9.2.3의 PolicyEngine은 **HookRunner 위에 구현**한다.

```python
# gapt/agent/hooks/policy_hook.py (의사코드)
from geny_executor.hooks import HookRunner
from geny_executor.tools.errors import ToolFailure, ToolErrorCode

class PolicyHook:
    def __init__(self, policy_engine, audit, scope):
        self._engine = policy_engine
        self._audit = audit
        self._scope = scope

    async def pre_tool_use(self, event):
        action = self._map_to_action(event.tool_name, event.tool_input)
        decision = await self._engine.evaluate(
            action=action,                  # "tool.bash" / "deploy.prod" / "secret.read" / ...
            actor=event.context.actor,      # agent_session
            scope=self._scope,
            context={"tool_input": event.tool_input, "tool_name": event.tool_name},
        )
        await self._audit.log(action="agent.policy.evaluate",
                              decision=decision.value, ...)
        if decision == PolicyDecision.DENY:
            raise ToolFailure("Policy denied", code=ToolErrorCode.ACCESS_DENIED)
        if decision == PolicyDecision.REQUIRE_USER_APPROVAL:
            await self._await_user_approval(event)  # 클라이언트에 SSE prompt
        if decision == PolicyDecision.REQUIRE_2FA:
            await self._await_2fa(event)
```

세션 부팅 시:
```python
runner = HookRunner()
runner.on_pre_tool_use(policy_hook.pre_tool_use)
runner.on_pre_tool_use(audit_hook.pre_tool_use)
runner.on_post_tool_use(audit_hook.post_tool_use)
runner.on_post_tool_use(cost_hook.post_tool_use)
runner.on_post_tool_failure(audit_hook.post_tool_failure)

pipeline.attach_runtime(hook_runner=runner)
```

**중요**: `claude_code_cli`를 쓰면 *CLI 내부 도구 호출*은 Stage 10을 거치지 않으므로 우리의 PRE_TOOL_USE 훅도 *기본적으로 발화 안 함*. 두 경로로 보강:

1. **CLI의 `settings_path` permission allow-list**: 첫 게이트. CLI의 권한 시스템이 1차 거부.
2. **MCP bridge의 자체 검증**: GAPT 도구(`mcp__gapt__*`) 호출은 *전부 우리 bridge를 거치므로* bridge 안에서 PolicyEngine 재평가. CLI built-in(Bash 등)은 settings_path 화이트리스트로만 통제.

이 2단계가 *CLI provider에서도 정책이 유효*한 이유.

> **M0-P3 PR4 empirical verification**: `poc/executor_agent/decision_two_layer_policy.md` 에 실제 PoC 실행 trace + `s10_tool/artifact/default/routers.py:262` 의 단일 `PRE_TOOL_USE.fire` 호출 위치 인용 + ascii 게이트 다이어그램 (Layer 1 / 2a / 2b) 정리. 후속 cycle 의 PolicyEngine 구현 시 이 문서를 1차 참고.

---

## 4.7 ProjectAwareSessionManager — 라이프사이클

```python
class ProjectAwareSessionManager:
    def __init__(
        self,
        environment_service: GaptEnvironmentService,  # manifest resolve
        sandbox_backend: SandboxBackend,
        secret_vault: SecretBackend,
        policy_engine: PolicyEngine,
        audit: AuditSink,
    ): ...

    async def create_session(
        self,
        project: Project,
        workspace: Workspace,
        user: User,
        env_id: str = "gapt_default",
    ) -> AgentSession:
        # 1. workspace의 sandbox가 살아있는지 확인
        sandbox = await self._sandbox.ensure(workspace)

        # 2. 단명 bridge token 발급
        session_id = ulid()
        bridge_token = mint_short_lived_token(session_id, ttl_s=21600)

        # 3. CredentialBundle 구성 (Vault → bundle, 시크릿 평문은 메모리에만)
        credentials = build_credentials(project, workspace, session, self._secret_vault)

        # 4. AdhocToolProvider 구성 (GAPT 도구가 sandbox/daemon을 알도록)
        tool_provider = GaptToolProvider(
            sandbox_ref=sandbox.ref,
            daemon_client=self._daemon_for(sandbox),
            git_service=self._git,
            deploy_orchestrator=self._deploy,
        )

        # 5. EnvironmentService가 manifest를 resolve → Pipeline 인스턴스화
        pipeline = await self._env.instantiate_pipeline(
            env_id=env_id,
            credentials=credentials,
            adhoc_providers=[tool_provider],
        )

        # 6. HookRunner 부착 (Policy + Audit + Cost)
        runner = self._build_hook_runner(project, workspace, session_id)
        pipeline.attach_runtime(hook_runner=runner)

        # 7. EventBus 구독 — token/cost/tool 이벤트를 audit로
        pipeline.on("api.*", self._audit_api_event)
        pipeline.on("tool.*", self._audit_tool_event)
        pipeline.on("stage.error", self._audit_error_event)

        # 8. AgentSession 객체 + 로컬 registry 등록
        session = AgentSession(id=session_id, pipeline=pipeline, project=project,
                               workspace=workspace, user=user, env_id=env_id,
                               credentials=credentials)
        self._sessions[session_id] = session
        await self._audit.log(action="agent.session.create", ...)
        return session

    async def stream(self, session_id, user_input) -> AsyncIterator[AgentEvent]:
        session = self._sessions[session_id]
        async for event in session.pipeline.run_stream(user_input):
            yield event  # SSE로 클라이언트에
```

### 4.7.1 세션 라이프사이클

| 상태 | 의미 | 트리거 |
|---|---|---|
| `creating` | manifest resolve + Pipeline 인스턴스화 중 | `create_session()` |
| `idle` | Pipeline 살아있고 대기 중 | 응답 완료 |
| `active` | `run_stream` 진행 중 | 사용자 메시지 |
| `paused` | 사용자가 명시 중단 또는 idle timeout | UI 인터럽트 / TickEngine |
| `archived` | 종료 + 메시지 보존 | 사용자 명시 / freshness policy |

`docs/architecture.md`의 `PipelineState` + `EventBus`를 그대로 활용. 세션별 별도 상태 머신 만들지 않음 — Pipeline의 생명주기에 piggyback.

### 4.7.2 Stale 정책

`docs/memory.md`의 메모리 압축이 자동으로 다뤄주므로, GAPT 측 stale 정책은 *sandbox 자원 회수* 위주:
- 5분 idle → 그대로
- 30분 idle → 경고 (UI 토스트)
- 6시간 idle → sandbox `paused` 상태로 전환 (Pipeline 객체는 살림)
- 24시간 idle → Pipeline `archived` + sandbox `stopped`

TickEngine 백그라운드 1분 간격.

---

## 4.8 비용 / 사용량 / 감사

### 4.8.1 데이터 소스

geny-executor 2.1.0의 매 turn마다 발행되는 이벤트:
- `api.*` (Stage 6): `{provider, model, ...}`
- `stage.exit` (Stage 7 Token): `{input_tokens, output_tokens, cache_read, cache_write, cost_usd}`
- `tool.call_start` / `tool.call_complete` (Stage 10): `{tool_name, duration_ms, outcome}`
- `pipeline.error` / `stage.error`: `{code, message, exception_type}`

GAPT의 `audit_hook` + `cost_hook`이 이걸 받아 다음으로 라우팅:
- `AuditEvent` (action=`agent.token.spend`, scope=project/workspace/session)
- `cost_tracker` 메모리 카운터 (UI 라이브 표시, 1초 디바운스)
- (옵션) OTel `gen_ai.usage.*` 메트릭

### 4.8.2 예산 통제

manifest의 `cost_budget_usd`가 Stage 4 Guard `cost_budget` 전략에 의해 강제. 초과 시 `GuardRejectError(code="exec.stage.guard_rejected")` → 사용자에게 명시 + override (정책 완화는 owner-only).

추가 cap (GAPT 측):
- `Project.cost_cap_per_day_usd`
- `Project.cost_cap_per_session_usd` (→ manifest `cost_budget_usd`로 자동 매핑)
- `Environment.cost_multiplier` (prod에서 더 엄격)

### 4.8.3 audit 페이로드 (정정)

기존 04에서 정의한 audit 형식 그대로지만, `exec.*.*` 코드를 항상 포함:

```json
{
  "id": "01HZ4XPP2K8MEXAMPLE",
  "actor": {"type": "agent_session", "id": "..."},
  "scope": {"project_id": "...", "workspace_id": "...", "env_id": "gapt_default"},
  "action": "agent.tool_invoke",
  "tool": "mcp__gapt__gapt_git",
  "args_hash": "sha256:...",
  "args_summary": "git commit -m '...'",
  "outcome": "ok",
  "duration_ms": 142,
  "policy_decision": "ALLOW",
  "exec_code": null,
  "ts": "2026-05-22T09:15:23.421Z"
}
```

`outcome=error`의 경우 `exec_code`에 안정 식별자 (`exec.api.rate_limited` / `exec.cli.permission_denied` 등) 그대로 기록 — 자체 코드 정의 안 함.

---

## 4.9 런타임 mutation — `PipelineMutator`

사용자가 채팅 중 권한 모드를 바꾸거나, 도구 화이트리스트를 토글하거나, 모델을 갈아탈 때:

```python
from geny_executor import PipelineMutator

mut = PipelineMutator(session.pipeline)
await mut.update_config(stage_order=6, config={"model": "opus"})       # 모델 변경
await mut.swap_strategy(stage_order=2, slot_name="loader",
                         impl="vector_search")                         # context 전략 변경
await mut.swap_strategy(stage_order=15, slot_name="gate", impl="gated") # Plan 모드 ON
```

`MutationLocked`(stage 실행 중)인 경우 클라이언트에 알림 → 다음 stage 경계에 재시도. mutation 직후 `pipeline.snapshot().to_manifest()`로 새 manifest 저장 가능.

**중요**: 사용자가 *manifest 자체*를 편집한 결과는 — 새 세션부터 적용. 라이브 세션을 *완전히 다른 manifest*로 swap하지 않음 (가능은 하나 혼란).

---

## 4.10 에러 처리 — `exec.*.*` 코드 그대로

geny-executor 2.1.0의 `ExecutorErrorCode`가 *이미 우리가 필요한 모든 에러*를 커버. GAPT는 자체 에러 계층 X. 대신:

| 코드 | UI 표시 (영) | UI 표시 (한) | 사용자 액션 |
|---|---|---|---|
| `exec.api.auth.invalid_key` | API key invalid | API 키가 잘못됨 | 시크릿 재등록 |
| `exec.api.rate_limited` | Rate limited, retrying… | 호출 한도, 재시도 중… | 대기 (자동) |
| `exec.api.timeout` | API timeout, retrying… | API 타임아웃, 재시도 중… | 대기 |
| `exec.api.token_limit` | Context too large | 컨텍스트 초과 | 메모리 압축 / 더 큰 모델 |
| `exec.cli.binary_not_found` | claude CLI not found | claude CLI 없음 | Runtime 이미지 확인 |
| `exec.cli.auth_failed` | claude not authenticated | claude 인증 안 됨 | OAuth 재로그인 |
| `exec.cli.timeout` | CLI subprocess timeout | CLI 타임아웃 | 재시도 |
| `exec.cli.permission_denied` | CLI permission denied | CLI 권한 거부 | `settings_path` allow-list 검토 |
| `exec.stage.guard_rejected` | Budget/cost limit | 예산/한도 초과 | cap 조정 또는 종료 |
| `exec.tool.access_denied` | Policy denied | 정책 거부 | PolicyEngine config 검토 |
| `exec.mutation.locked` | Stage busy, retrying… | 단계 진행 중, 재시도 | 자동 |
| `exec.mcp.connect_failed` | MCP server unreachable | MCP 서버 도달 불가 | bridge 프로세스 확인 |

이 표는 `gapt-web`의 i18n catalog에 그대로 들어감 — `docs/error_codes.md`의 *안정성 계약*에 따라 string 값은 영구 변경 안 됨.

---

## 4.11 LLM 백엔드 인터페이스 — 단일 구현 원칙 (정정)

03 문서의 `LlmAgentBackend` Protocol에 대한 우리의 입장 정정:

- **`LlmAgentBackend` Protocol을 *유지하지 않는다*가 더 정확.**
- GAPT 에이전트 레이어의 진입점은 `ProjectAwareSessionManager`이고, 그것이 직접 `Pipeline.from_manifest_async`를 호출.
- 테스트는 *manifest mock + MockProvider* 패턴으로 충분 — `docs/architecture.md`가 명시한 `MockProvider`를 manifest에서 지정 가능.
- 즉 **두 번의 추상화 레이어를 두지 않는다** — geny-executor가 이미 추상화의 정점.

→ [03](03_system_architecture.md) §3.6에서 D5 Agent Session 어댑터 인터페이스가 *명시적으로 제외*되어 있음 (이미 정정 완료).

신규 모델/도구가 필요해질 때 — `geny-executor의 *어디*에 PR하면 되는지* 표:

| 필요 | 자리 |
|---|---|
| 새 LLM provider (예: AWS Bedrock 직접) | `llm_client/` + `Stage 6` provider 등록 |
| 새 도구 카테고리 | `tools/built_in/` 추가 + manifest로 enable |
| 새 메모리 백엔드 (예: Qdrant) | `memory/providers/` 추가 + manifest로 선택 |
| 새 sub-agent orchestration 패턴 | `Stage 12` 새 strategy |
| 새 평가 전략 | `Stage 14` 새 strategy |
| 새 가드(예: PII 검사) | `Stage 4` 새 guard |

전부 *executor 본체에서 PR*. GAPT 코드는 manifest 편집으로 받음. 이게 [[feedback_extend_executor_not_adapter_layer]] 의 구체적 적용.

---

## 4.12 멀티 세션 / 동시성

### 세션 단위
- 1 워크스페이스 안에서 *여러* 세션 동시 가능 (`docs/architecture.md`의 `PipelineState`가 세션별로 격리).
- 각 세션은 독립 Pipeline 인스턴스 + 독립 HookRunner.
- 같은 sandbox + 같은 CredentialBundle 공유 — 토큰은 사용량으로 합산.

### 동시 도구 호출
같은 워크스페이스 두 세션이 동시에 Edit하면:
- 데몬이 파일 advisory lock (`flock`).
- Edit 결과는 git status로 자동 검증.
- 충돌 시 후행 세션이 `ToolFailure` → 사용자 해결 유도.

### 인터럽트
- `POST /api/sessions/{sid}/interrupt` → `pipeline.cancel()` (Pipeline 내부 cancellation token).
- 진행 중 도구 (`Bash`)는 데몬이 SIGTERM → 5초 후 SIGKILL.
- Stage 6의 inflight API 호출은 SDK 수준 cancel.

---

## 4.13 Geny와의 관계 (오해 정정)

이전 04 작성 시 *Geny의 agent_session_manager의 특수 패턴*(VTuber/Tamagotchi/CharacterPersonaProvider/TickEngine 등)을 GAPT가 차용하는 양 적었는데, 사실 정정:

- **Geny와 GAPT는 같은 *상위 도구*(geny-executor)를 쓰는 *별개 호스트*.**
- Geny의 VTuber/Tamagotchi/Persona는 *Geny의 도메인 로직*이지 GAPT와 무관.
- GAPT가 빌리는 것은 *패턴*뿐:
  - `EnvironmentService.instantiate_pipeline(env_id, credentials, ...)` — 같은 함수명, 같은 시그너처. Geny 검증 패턴 그대로.
  - MCP stdio bridge ~150 LoC — Geny의 `geny_mcp_bridge.py` 구조 참고.
  - 매니저 스코프 + 라이프사이클 이벤트 버스 — 단, *Geny의 lifecycle 이벤트 enum 그대로 가져오지 않는다* (GAPT는 자기 도메인 이벤트 정의).
- GAPT는 Geny의 코드를 *import 하지 않는다*. geny-executor만 의존.

---

## 4.14 보장하는 인터페이스

1. **GAPT 에이전트 레이어는 geny-executor 2.1.0 이상에만 의존.** 자체 에이전트/모델/도구 다층화 X.
2. **모든 세션은 `EnvironmentManifest` JSON 한 곳에서 정의** — 라이브 mutation은 `PipelineMutator`만 사용.
3. **1차 provider = `claude_code_cli`** + **host MCP wrap으로 GAPT 도구 노출** — `gapt_default.json`이 진입점.
4. **PolicyEngine = HookRunner의 `PRE_TOOL_USE` 위 구현.** SDK provider 경로 + MCP bridge 안 재평가 2단계로 CLI provider에도 적용.
5. **에러 코드는 `exec.*.*` 그대로** — 자체 정의 금지, UI i18n 1:1 매핑.
6. **manifest 단일 진실원** — provider/모델/iter cap/cost cap 각 1곳에만, strict-load.
7. **신규 LLM/도구/스트래티지 추가는 geny-executor 본체에 PR** — GAPT는 의존 버전만 올림.

다음 [05](05_git_workflow.md)는 *Git 통합* — GAPT 도구 중 `gapt_git`/`gapt_pr`의 외부 git 호스트 처리.
