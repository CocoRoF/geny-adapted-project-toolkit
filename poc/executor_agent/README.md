# poc/executor_agent/

> Plan: [`../../docs/plan/m0/p3_agent_mcp_bridge.md`](../../docs/plan/m0/p3_agent_mcp_bridge.md)
> Progress: [`../../docs/progress/m0/p3_agent_mcp_bridge.md`](../../docs/progress/m0/p3_agent_mcp_bridge.md)

M0-P3 PoC: drive `geny-executor` 2.1.0's `Pipeline.from_manifest_async` against the `claude_code_cli` provider, with a host MCP wrap so the CLI's internal agent can call the *host's* tool registry as `mcp__gapt__<tool>`. Verifies the manifest-driven path GAPT's `D5 Agent Session Manager` will own in M1-E2.

This PoC runs on the **host** (not inside a sandbox) — the sandbox layer comes back in M1-E1 cycle 1.7 (`SandboxBackend`). Keeping the PoC host-local lets us isolate the "agent + MCP" question from the "agent inside Sysbox" question.

## Prereqs

- `claude` CLI 2.1.126+ on `PATH` (with `~/.claude/.credentials.json` populated by `claude auth login` — OAuth subscription path) **or** `ANTHROPIC_API_KEY` env var (API key path)
- `uv` 0.4+ on `PATH`

## Run

```bash
cd poc/executor_agent
uv sync --extra dev

# PR1 of M0-P3 only verifies the env: claude reachable + executor importable
uv run python -c "import geny_executor; print(geny_executor.__version__)"
uv run python -c "import subprocess; print(subprocess.check_output(['claude', '--version'], text=True).strip())"

# PR2+ adds run.py for the actual smoke
```

## Layout

```
poc/executor_agent/
├── pyproject.toml
├── README.md
├── manifests/
│   └── gapt_default.v0.json     # PR2
├── credentials.py               # PR2 — CredentialBundle builder
├── policy_hook.py               # PR4 — PRE_TOOL_USE veto
├── host.py                      # PR3 — tool catalogue + dispatcher
├── run.py                       # PR2 — Pipeline.from_manifest_async smoke
├── audit.jsonl                  # populated at runtime
├── decision_two_layer_policy.md # PR4
├── error_codes_reproduced.md    # PR5
└── scripts/run_poc.sh           # PR6 integration
```

## Sibling

`poc/mcp_bridge/server.py` (PR3): the stdio JSON-RPC loop that the spawned `claude` CLI talks to, surfacing this PoC's tools to the CLI as `mcp__gapt__<tool>`.
