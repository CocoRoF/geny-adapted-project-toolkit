# Decision: two-layer tool-policy gate for `claude_code_cli`

> M0-P3 PR4 finding
> Date: 2026-05-23
> Owner: gkfua00 (CocoRoF)
> Status: **adopted**
> Evidence: this PoC run (commit *this commit*), audits at
> `poc/executor_agent/audit_hooks.jsonl` + `poc/mcp_bridge/bridge_audit.jsonl`

## Context

`docs/04_llm_agent_layer.md` §4.6 / `docs/09_security_authz_observability.md`
§9.2.3 prescribe a `PolicyEngine` that gates every tool call before
dispatch. The naïve reading is: register one `HookRunner.PRE_TOOL_USE`
hook on the geny-executor pipeline, and every tool the LLM tries to call
will be checked.

That reading is **wrong** for the `claude_code_cli` provider — and
`claude_code_cli` is GAPT's default LLM provider. PR4 of M0-P3 sets the
record straight before later cycles inherit a bug.

## Empirical setup

`poc/executor_agent/run_hooks.py` wires:

1. A `HookRunner` (via `policy_hook.build_runner`) with:
   - an in-process `HookEvent.PRE_TOOL_USE` handler that records every
     fire and would return `HookOutcome(decision="deny")` for the tool
     name `gapt_unsafe`.
   - an audit callback recording every hook fire to `audit_hooks.jsonl`.
2. `pipeline.attach_runtime(hook_runner=runner)` so the pipeline holds
   a reference to the runner before `pipeline.run()`.
3. The MCP stdio bridge from PR3, with its own in-process policy hook
   that always denies `gapt_unsafe` and writes to `bridge_audit.jsonl`.

Prompt: "Call `mcp__gapt__gapt_hello` then `mcp__gapt__gapt_unsafe` and
summarise each response."

## Observed result

| File | Records that matter |
|---|---|
| `audit_hooks.jsonl` (35 records) | 1× `pipeline.start`, 12× `stage.enter`, 12× `stage.exit`, 9× `stage.bypass`, 1× `pipeline.complete`. **0× `pre_tool_handler.called`, 0× `hook_runner.audit`.** |
| `bridge_audit.jsonl` (5 records) | `tools/list`, `tools/call gapt_hello`, `tools/call.ok gapt_hello`, `tools/call gapt_unsafe`, `tools/call.denied gapt_unsafe (exec.tool.access_denied)` |
| LLM final response | Summarises both tools correctly: `gapt_hello` returned a greeting; `gapt_unsafe` returned a policy-denial. |

The CLI's LLM made **two tool calls**, the bridge handled both, and the
pipeline-side PRE_TOOL_USE handler did not fire a single time.

## Why this happens

`HookEvent.PRE_TOOL_USE` is fired by **exactly one** call site in
geny-executor 2.1.0:

```
geny_executor/stages/s10_tool/artifact/default/routers.py:262
    pre_outcome = await runner.fire(HookEvent.PRE_TOOL_USE, pre_payload)
```

That call site lives inside Stage 10's tool router. For
`claude_code_cli`, the entire agentic loop — including tool dispatch —
happens **inside the spawned CLI subprocess via MCP / built-in tool
machinery**. The pipeline's Stage 10 is bypassed (the audit shows 9
bypass events). The LLM's tools never traverse Stage 10, so the hook
runner attached pipeline-side never sees them.

This is not a bug in geny-executor. It's a property of running the
agentic loop in a subprocess: the LLM's choice of tools, the MCP
transport, and the tool-call dispatch are all *inside the CLI* by
design. The pipeline owns lifecycle and observability, not in-loop
gating.

## Decision: two layers, each with a different gate

```
┌────────────────────────────────────────────────────────────────┐
│ Layer 1: pipeline-side PolicyEngine (HookRunner.PRE_TOOL_USE)  │
│ Active for: anthropic / openai / google / vllm direct providers│
│ Active for: claude_code_cli  →  NO  (Stage 10 bypassed)        │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼  (claude_code_cli only)
┌────────────────────────────────────────────────────────────────┐
│ Layer 2a: CLI built-in permissions (settings_path allow-list)  │
│ Gates Read / Bash / Edit / etc. inside the CLI subprocess.     │
│ Enforced by Claude Code CLI itself; surfaces as                │
│   `exec.cli.permission_denied` if the LLM tries a denied tool. │
│ GAPT controls it via `extras["settings_path"]` JSON.           │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Layer 2b: MCP bridge in-process policy (this PoC's bridge)     │
│ Gates `mcp__gapt__*` tools — every MCP call from the CLI       │
│ traverses the bridge first. The bridge owns the policy hook    │
│ closest to the actual side-effecting code on the host side.    │
│ Result is returned to the CLI as a structured tool response    │
│ (text + isError-equivalent) so the LLM can react to it.        │
└────────────────────────────────────────────────────────────────┘
```

Implication for GAPT design:

- **Treat `HookRunner.PRE_TOOL_USE` as Layer 1 only.** Don't claim it
  protects against CLI-internal tool misuse. Document this explicitly
  in `docs/09_security_authz_observability.md` §9.2.3.
- **MCP bridge in-process policy is the load-bearing gate for
  host-attached tools** in the `claude_code_cli` path. It must be
  implemented in the *bridge*, not the pipeline. PR3 has the simplest
  possible form (hard-coded deny for `gapt_unsafe`). M1-E2 will swap
  this for the real `PolicyEngine` (default deny + config patterns,
  per `feedback_policy_config_not_hardcode`).
- **CLI built-in tools are gated by `settings_path` allow-list**. The
  PoC sets it to `{"permissions":{"allow":["mcp__gapt","Read","Glob","Grep"]}}`.
  Anything outside that list — `Bash`, `Edit`, `Write`, etc. — is
  refused by the CLI itself before it ever hits a host. The refusal
  surfaces in the CLI stream as a permission event. PR5 will reproduce
  this as `exec.cli.permission_denied`.
- **The pipeline's `EventBus` + `HookRunner.set_audit_callback` still
  give us full pipeline-lifecycle observability** (stage entry/exit,
  pipeline start/complete, API request/response). They just don't see
  per-tool gating decisions for `claude_code_cli`.

## What this means for M1-E2

When the executor session is split into a proper host RPC (M1-E2-PR3-ish),
the MCP bridge becomes the layer where the centrally-defined
`PolicyEngine` is *enforced*, even though the engine itself can live in
the host process and be configured by `policy.yaml`. The bridge's role:
take each `tools/call`, ship it to the host's `PolicyEngine.evaluate()`,
honour the answer.

Pipeline-side `HookRunner.PRE_TOOL_USE` becomes the gate **only for the
direct-API providers** in the same generalised executor abstraction —
useful for offline test runs and for non-CLI use cases that may emerge,
but not the primary policy surface for GAPT's default `claude_code_cli`
path.

## What does NOT change

- `feedback_policy_config_not_hardcode` still applies — the bridge's
  policy hook must be data-driven (config patterns), not hard-coded
  beyond the PoC stage.
- `feedback_extend_executor_not_adapter_layer` still applies — the
  bridge belongs in `geny-executor` (or a slim companion package),
  not in an app-side adapter layer. PR4 keeps the PoC bridge under
  `poc/mcp_bridge/`; M1-E2 promotes it.

## Follow-ups created by this decision

1. Update `docs/09_security_authz_observability.md` §9.2.3 with the
   two-layer figure above (queued for M0-P3 PR6 close-out).
2. Update `docs/04_llm_agent_layer.md` §4.6 to call out
   `claude_code_cli` exception explicitly.
3. PR5 reproduces `exec.cli.permission_denied` by removing
   `Read`/`Glob`/`Grep` from the allow-list and prompting the LLM to
   try them.
