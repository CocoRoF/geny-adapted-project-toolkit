# gapt-runtime

> Base container image + `toolkit-agent` daemon that runs *inside* each per-project Sysbox sandbox. The daemon mediates between the control plane and the sandbox interior (RPC over unix socket).

Status: **M0-P1 PR3 — skeleton only.** Daemon serves `/health` + `/info`; tool dispatch / PTY / RPC arrive in M1-E1 cycle 1.9.

## Why this exists

The control plane (`server/`) never `docker exec`'s into the sandbox. Instead it RPCs to a small Python daemon (`toolkit-agent`) running inside, which dispatches tools, opens PTYs, and reads/writes files within the workspace. This indirection:

- keeps host docker socket entirely off the container surface (T6 per [`docs/06_isolation_and_runtime.md`](../docs/06_isolation_and_runtime.md))
- gives one stable RPC contract regardless of K8s vs Docker backend (M4+)
- centralises audit emission at the container side

## Image: `gapt/runtime`

`Dockerfile` (multi-stage) ships:

- Debian bookworm-slim base
- `git`, `git-lfs`, `gh` CLI (GitHub)
- Docker CE + compose-plugin (inner dockerd via Sysbox safe DinD)
- Python 3.12 + `uv` (deterministic Python deps)
- Node 22 LTS + pnpm + yarn (via corepack)
- `toolkit-agent` daemon installed from this very package

**Not** bundled: `claude` CLI itself. The host orchestrator mounts or installs it per-session so authentication stays user-driven.

## Daemon

```
runtime/
├── pyproject.toml
├── Dockerfile
├── scripts/entrypoint.sh        # boots inner dockerd then exec's daemon
├── src/gapt_runtime/
│   ├── __init__.py              # __version__
│   ├── settings.py              # DaemonSettings.from_env()
│   ├── daemon.py                # aiohttp app with /health + /info
│   ├── cli.py                   # `toolkit-agent serve|version`
│   └── py.typed
└── tests/
    ├── test_daemon_smoke.py
    └── test_settings.py
```

### Develop the daemon locally

```bash
cd runtime
uv sync --extra dev

uv run toolkit-agent version
uv run pytest

uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

### Build the container

```bash
# From repo root:
docker build -f runtime/Dockerfile -t gapt/runtime:dev .
docker run --rm gapt/runtime:dev toolkit-agent version
```

For real isolation testing (Sysbox + SeaweedFS Mount) see [`docs/plan/m0/p2_isolation_seaweedfs.md`](../docs/plan/m0/p2_isolation_seaweedfs.md).

## Env vars consumed by the daemon

| var | default | purpose |
|---|---|---|
| `GAPT_AGENT_SOCKET` | `/run/agent.sock` | Unix socket to listen on (control plane connects here) |
| `GAPT_DAEMON_TOKEN` | *(empty)* | Short-lived JWT issued by control plane on sandbox boot |
| `GAPT_PROJECT_ID` | *(unset)* | Owning project — surfaced to tools / audit |
| `GAPT_WORKSPACE_ID` | *(unset)* | Owning workspace |
| `GAPT_SESSION_ID` | *(unset)* | Owning agent session (when applicable) |
| `GAPT_WORKSPACE_ROOT` | `/workspace` | SeaweedFS-mounted workspace path |

## Plan ↔ code mapping

- `docs/plan/m0/p1_monorepo_ci.md` cycle 3 (this PR)
- `docs/plan/m0/p2_isolation_seaweedfs.md` (image used for isolation PoC)
- `docs/plan/m1/e1_backend_foundation.md` cycle 1.9 (RPC contract + PTY)
- `docs/plan/m1/e2_agent_and_git.md` cycle 2.3 (MCP bridge promoted into this image)
