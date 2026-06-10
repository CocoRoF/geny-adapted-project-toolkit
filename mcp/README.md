# gapt-mcp

MCP (Model Context Protocol) server for **GAPT** — geny-adapted-project-toolkit,
a self-hosted project-sandbox platform. Point any MCP client (Claude Code,
Claude Desktop, Cursor, …) at your GAPT instance and the agent can:

- browse projects, attach git repositories (multi-repo), create **sandboxed
  workspaces** with per-repo branch selection
- read/write files, run arbitrary shell commands inside the sandbox
- drive git per repository — status/log/branches/checkout/commit/sync/stash/PR
- start & expose dev servers (preview URLs through the GAPT gateway)
- delegate whole coding tasks to GAPT's **built-in coding agent**
- deploy environments, watch runs, inspect stacks, roll back
- check LLM spend

41 tools, one skill document (`gapt://skill/usage`), zero local state.

## Quick start

```bash
npx gapt-mcp   # requires the three env vars below
```

| env | meaning |
| --- | --- |
| `GAPT_BASE_URL` | where GAPT runs, e.g. `https://gapt.example.com` |
| `GAPT_LOGIN_ID` | admin login id |
| `GAPT_LOGIN_PW` | admin login password |
| `GAPT_TIMEOUT_MS` | optional per-request timeout (default 60000) |

Auth is the GAPT session cookie; the server logs in lazily and re-logins
automatically when the session expires.

### Claude Code

```bash
claude mcp add gapt \
  --env GAPT_BASE_URL=https://gapt.example.com \
  --env GAPT_LOGIN_ID=admin \
  --env GAPT_LOGIN_PW=secret \
  -- npx gapt-mcp
```

or `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "gapt": {
      "command": "npx",
      "args": ["gapt-mcp"],
      "env": {
        "GAPT_BASE_URL": "https://gapt.example.com",
        "GAPT_LOGIN_ID": "admin",
        "GAPT_LOGIN_PW": "secret"
      }
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gapt": {
      "command": "npx",
      "args": ["-y", "gapt-mcp"],
      "env": {
        "GAPT_BASE_URL": "https://gapt.example.com",
        "GAPT_LOGIN_ID": "admin",
        "GAPT_LOGIN_PW": "secret"
      }
    }
  }
}
```

## Tool catalog

| group | tools |
| --- | --- |
| orient | `gapt_overview`, `cost_summary` |
| projects | `list_projects`, `get_project`, `create_project`, `list_repositories`, `add_repository`, `list_remote_branches` |
| workspaces | `list_workspaces`, `create_workspace`, `get_workspace`, `manage_workspace` (start/stop/delete/rehydrate), `run_command` |
| files | `list_files`, `read_file`, `write_file`, `delete_path`, `git_diff_file` |
| git (per-repo) | `git_status`, `git_log`, `git_branches`, `git_checkout`, `git_commit`, `git_sync` (fetch/pull/push/sync), `git_discard`, `git_stash`, `create_pr` |
| services | `list_services`, `start_service`, `manage_service` (stop/restart/remove/expose/unexpose) |
| agent | `agent_oneshot`, `create_agent_session`, `agent_invoke`, `agent_messages`, `agent_interrupt` |
| deploy | `list_environments`, `deploy_environment`, `get_deploy_run`, `list_deploy_runs`, `environment_stack` (status/logs/restart/down), `rollback_environment` |

**Multi-repo note** — every `git_*` tool takes `repo_id`
(from `list_repositories`). Always pass it on multi-repo projects; omitting
falls back to the project's primary repository.

## The skill

The full usage guide (core model, golden workflows, error recovery) ships
three ways so any client can consume it:

1. MCP server `instructions` — injected automatically at connect
2. resource `gapt://skill/usage`
3. prompt `gapt_usage_guide`

## Development

```bash
cd mcp
npm install
npm run typecheck
npm run build
npm run smoke                       # tools/resources/prompts listing
GAPT_SMOKE_LIVE=1 npm run smoke     # + live gapt_overview call
node scripts/live-deep.mjs          # deep live exercise (needs an instance)
```

## Publish

```bash
npm login          # once
npm publish        # prepublishOnly builds dist/
```

or trigger the `mcp-publish` GitHub Actions workflow (needs `NPM_TOKEN`
repo secret).

## License

Apache-2.0
