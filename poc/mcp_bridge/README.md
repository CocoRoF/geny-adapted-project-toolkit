# poc/mcp_bridge/

> Plan: [`../../docs/plan/m0/p3_agent_mcp_bridge.md`](../../docs/plan/m0/p3_agent_mcp_bridge.md) §3 / §4
> Progress: [`../../docs/progress/m0/p3_agent_mcp_bridge.md`](../../docs/progress/m0/p3_agent_mcp_bridge.md)

MCP stdio JSON-RPC server spawned by the `claude` CLI through its `extras["mcp_config"]`. Surfaces two PoC tools to the spawned CLI's LLM:

- `gapt_hello(name)` — happy-path echo. Demonstrates `mcp__gapt__gapt_hello` calls landing in the bridge and the result coming back to the LLM.
- `gapt_unsafe(cmd)` — always returns a "policy denied" text. Demonstrates the *denied* call path so we can see how the LLM handles a refusal. M0-P3 PR4 wires this through a HookRunner so the same denial flows through pipeline-side audit.

## Run standalone (sanity)

```bash
cd poc/mcp_bridge
uv sync --extra dev
uv run python server.py     # waits for MCP JSON-RPC on stdin/stdout (Ctrl+C to exit)
```

## How the executor_agent PoC consumes it

`poc/executor_agent/credentials.py` will attach this as the spawned CLI's MCP server (M0-P3 PR3.5+):

```python
mcp_config = {
    "mcpServers": {
        "gapt": {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "python", "<repo>/poc/mcp_bridge/server.py"],
            "env": {"GAPT_BRIDGE_AUDIT": "<repo>/poc/mcp_bridge/bridge_audit.jsonl"},
        }
    }
}
```

`run.py` calls `build_credentials(mcp_config=mcp_config, settings_path='{"permissions":{"allow":["mcp__gapt","Read"]}}', ...)`.
