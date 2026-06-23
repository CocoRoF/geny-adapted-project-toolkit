# gapt-server

> Control plane for GAPT. FastAPI + asyncio. Calls `geny-executor 2.1.0+` via `Pipeline.from_manifest_async` — no LLM adapter layering of its own.

Status: **M0-P1 PR2 — skeleton only.** `/health` responds, settings load, structured logging configured. Real domains (Project / Auth / Sandbox / Secret / Audit / Agent / Deploy) land in M1-E1+.

## Layout

```
server/
├── pyproject.toml
├── src/gapt_server/
│   ├── __init__.py              # __version__
│   ├── app.py                   # create_app factory + lifespan
│   ├── settings.py              # pydantic-settings (GAPT_ env prefix)
│   ├── logging.py               # structlog config (json | console)
│   ├── py.typed
│   └── routers/
│       └── health.py            # / + /health
└── tests/
    ├── conftest.py              # AsyncClient via ASGITransport
    ├── test_health.py
    └── test_settings.py
```

## Develop locally

```bash
cd server
uv sync --extra dev

# Run
uv run uvicorn gapt_server.app:app --reload --host 0.0.0.0 --port 8080

# Test
uv run pytest

# Lint + type
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

## Settings

All env vars prefixed `GAPT_`. See `src/gapt_server/settings.py`. Examples:

| env var | default | purpose |
|---|---|---|
| `GAPT_ENV` | `dev` | `dev` / `staging` / `prod` |
| `GAPT_PORT` | `8080` | listen port |
| `GAPT_LOG_LEVEL` | `INFO` | logging level |
| `GAPT_LOG_FORMAT` | `json` | `json` or `console` |
| `GAPT_POSTGRES_DSN` | *(unset)* | PostgreSQL DSN — required from M1-E1 |
| `GAPT_REDIS_DSN` | *(unset)* | Redis DSN — required from M1-E1 |
| `GAPT_SEAWEED_FILER_URL` | *(unset)* | SeaweedFS Filer HTTP endpoint |
| `GAPT_SEAWEED_S3_ENDPOINT` | *(unset)* | SeaweedFS S3 endpoint |
| `GAPT_CLAUDE_BINARY_PATH` | `/usr/local/bin/claude` | path to `claude` CLI |
| `GAPT_DEFAULT_MANIFEST_ID` | `gapt_default` | which manifest to instantiate |
| `GAPT_SESSION_SECRET` | *(dev-only)* | session cookie HMAC — **override in prod** |
| `GAPT_DAEMON_JWT_SECRET` | *(dev-only)* | toolkit-agent JWT secret — **override in prod** |

## Plan ↔ code mapping

This skeleton corresponds to [`docs/plan/m0/p1_monorepo_ci.md`](../docs/plan/m0/p1_monorepo_ci.md) cycle 2 (server/ setup). M1-E1 cycles 1.1~1.12 will populate the actual domains.
