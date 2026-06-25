# geny-adapted-project-toolkit (GAPT)

> **A self-hosted AI DevOps platform.** An `OpenHands × Coolify` mashup you run on your own server, plus a `Cursor`-class live-editing UI. Take an external Git repo — `git clone` → **Docker (Sysbox) isolation** → **Claude Code-based LLM** — and do everything from editing to testing, building, and deploying in **a single web console**.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-M1--E4_complete_(beta)-yellow)](docs/11_roadmap.md)

> **M1-E4 complete (2026-05-24).** 350+ server tests · 89+ web tests · operator self-host ready (`compose/docker-compose.prod.yml`). Next: dogfood GAPT with GAPT itself + the first Geny adapt — operations guides at [`docs/operations/install.md`](docs/operations/install.md) and [`docs/operations/geny-adapt.md`](docs/operations/geny-adapt.md).

## The Geny ecosystem

These projects are built to work together. **Geny** is the product at the top of the stack; everything below is a building block you can also use on its own. **➡️ marks where you are.**

| Project | What it is | Role in the stack |
|---|---|---|
| [**Geny**](https://github.com/CocoRoF/Geny) | Multi-agent VTuber + autonomous-worker platform | The product — uses every project below |
| [**geny-executor**](https://github.com/CocoRoF/geny-executor) | 21-stage, manifest-driven agent pipeline · PyPI · MIT | The engine everything runs on |
| ➡️ [**GAPT**](https://github.com/CocoRoF/geny-adapted-project-toolkit) | Self-hosted AI DevOps platform — sandbox · edit · build · deploy | Where agents safely touch real repos |
| [**geny-avatar**](https://github.com/CocoRoF/geny-avatar) | 2D live-avatar editor with AI texture generation | Where Geny's faces are made |

<details>
<summary>How they fit together</summary>

```
                  Geny — the product (uses everything below)
                    │
      ┌─────────────┼──────────────┐
 agent engine    avatars      sandbox + deploy
      │             │              │
      ▼             ▼              ▼
 geny-executor  geny-avatar      GAPT
  (the engine)  (avatar editor)  (AI DevOps platform)
```

</details>

---

<!-- 📸 IMAGE NEEDED: hero screenshot — the GAPT web console: project tree + Monaco editor + agent chat + terminal + live preview in one tab -->
> 📸 **Image needed** — _hero screenshot: the GAPT web console — project tree, Monaco editor, agent chat, terminal, and live preview all in one browser tab._

---

## In one line

```
User → one browser tab → left-hand project tree (many external repos) → enter a workspace
   → Sysbox-isolated container + SeaweedFS persistent files + Claude Code CLI
   → chat / editor / terminal / preview / CI / deploy, all in the same session
```

Full vision: [`docs/00_overview.md`](docs/00_overview.md)

---

## GAPT is itself a TOOL — `gapt-mcp`

Because GAPT is an isolated sandbox + project-management platform, **the
whole thing can become a single tool.** **gapt-mcp** in [`mcp/`](mcp/) is
an npm package that exposes every core GAPT activity as an MCP (Model
Context Protocol) tool — any MCP client (Claude Code, Claude Desktop,
Cursor, …) can remote-control a GAPT instance.

```jsonc
// .mcp.json — these few lines let an agent drive all of GAPT
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

```bash
# Register with Claude Code in one line
claude mcp add gapt \
  --env GAPT_BASE_URL=https://gapt.example.com \
  --env GAPT_LOGIN_ID=admin --env GAPT_LOGIN_PW=secret \
  -- npx gapt-mcp
```

What a connected agent can do — **41 tools**:

| Group | Capability |
|---|---|
| orient | Grasp the whole instance in one `gapt_overview` call, with aggregated LLM cost |
| projects | Create projects (including empty ones) · attach **multiple git repos** · list remote branches |
| workspaces | Create a sandbox by picking a branch per repo · start/stop/delete/rehydrate · **run arbitrary shell commands** |
| files | Tree / read / write / delete + per-file diff |
| git | Per-repo status/log/branches/checkout/commit/sync/stash/discard/**open a PR** |
| services | Start a dev server · **expose** it via a gateway preview URL |
| agent | **Delegate a whole task** to GAPT's built-in coding agent (oneshot + session) |
| deploy | Async deploy · poll runs · stack logs/restart · **rollback** |

A usage skill (core model, golden workflows, error-recovery rules) is
embedded three ways — the server `instructions`, the
`gapt://skill/usage` resource, and the `gapt_usage_guide` prompt — so an
agent uses GAPT *correctly* the moment it connects. Details:
[`mcp/README.md`](mcp/README.md)

> What this structure means: **an agent on top of GAPT calls GAPT.** An
> external Claude creates a workspace via gapt-mcp, delegates work to the
> built-in agent inside it, turns the result into a PR, and deploys it —
> the whole flow is tool calls.

---

## Why build this

As of May 2026, essentially no single product offers all of the following *at once*.

| Requirement | Closest candidate | The gap vs. us |
|---|---|---|
| Self-host (run on your own server) | Coolify, OpenHands, Coder | OpenHands is *only* a code agent; Coolify is *only* deployment |
| AI code agent (Claude Code-class) | Cursor, Windsurf | SaaS-only |
| Multi-project (many external repos at once) | GitLab Duo | Only inside GitLab |
| Built-in CI/CD + deploy | GitHub Actions, Coolify | Decoupled from the code agent |
| IDE-like live-editing UI | Cursor, v0 | Not self-hostable, or vendor-locked |

Full market analysis: [`docs/01_market_landscape.md`](docs/01_market_landscape.md)

---

## Core principles

1. **Isolation by Default** — never expose the host Docker socket *in any mode*. Sysbox runtime first.
2. **External Repo is First-Class** — the GitHub/Gitea repos you already own are first-class citizens.
3. **No Vendor Lock-in** — the LLM, Git host, IDP, and secret store are all swappable adapters.
4. **Self-Host or Get Out** — the core runs 100% on a single node. SaaS add-ons are optional.
5. **Reuse Battle-Tested Components** — assembled from Sysbox, Caddy, Monaco, dockview, xterm, and geny-executor.
6. **Live Edit > Batch Generate** — Cursor-style small changes reflected instantly.
7. **Audit Everything** — every LLM call, tool, file edit, and deploy command is audit-logged.
8. **Policy by Default, Not by Code** — risky actions hit the *PolicyEngine's strict defaults*; you relax them via config, at your own responsibility.
9. **Single Agent Backend, Manifest-driven** — one place: `geny-executor 2.1.0+`. GAPT tools are exposed to the CLI via the **`claude_code_cli` provider + a host MCP wrap**.

Full principles: [`docs/00_overview.md`](docs/00_overview.md) §0.8

---

## Documentation structure

### Analysis (12 documents — the output of Phase 0)

| # | Title | One line |
|---|---|---|
| 00 | [Overview](docs/00_overview.md) | Vision, positioning, core values, non-goals, glossary |
| 01 | [Market landscape](docs/01_market_landscape.md) | 12+ competitors, the empty slot, patterns to borrow/avoid |
| 02 | [Use cases / personas](docs/02_use_cases_and_personas.md) | P1–P4, 5 golden paths, non-scenarios |
| 03 | [System architecture](docs/03_system_architecture.md) | Control/execution planes, 8 domains, data flow |
| 04 | [LLM agent layer](docs/04_llm_agent_layer.md) | geny-executor manifest-driven, MCP 2-boundary |
| 05 | [Git workflow](docs/05_git_workflow.md) | clone / worktree / PR / credential |
| 06 | [Isolation / runtime](docs/06_isolation_and_runtime.md) | Sysbox, Compose, resources, networking |
| 07 | [CI/CD / preview](docs/07_cicd_and_preview.md) | inner/outer loop, DeployTarget, Caddy |
| 08 | [Web IDE UX](docs/08_web_ide_ux.md) | Monaco + dockview, chat as first-class, Plan/Act |
| 09 | [Security / authz / audit / observability](docs/09_security_authz_observability.md) | RBAC, Vault, Audit, PolicyEngine, OTel |
| 10 | [Tech stack decisions](docs/10_tech_stack_decisions.md) | Decision matrix + licensing pitfalls |
| 11 | [Roadmap](docs/11_roadmap.md) | M0–M5 milestones + non-goals lifted over time |
| 12 | [Geny case study](docs/12_geny_case_study.md) | The first adapt in 9 steps + pitfalls |

### Plan / progress

- [`docs/plan/`](docs/plan/) — master plan + M0/M1 detail cards + M2–M5 outlines
- [`docs/progress/`](docs/progress/) — per-cycle progress log (updated per PR)

Cadence rules: [`docs/plan/00_master_plan.md`](docs/plan/00_master_plan.md) §0.1

---

## Code layout (target at end of M1)

```
geny-adapted-project-toolkit/
├── docs/                   # analysis + plan + progress
├── analysis/               # deep-dives on new topics (added freely mid-cycle)
├── compose/                # self-deployment compose (dev / prod)
├── server/                 # control plane (Python + FastAPI)
├── runtime/                # gapt/runtime container image + daemon
├── web/                    # frontend (Vite + React)
├── mcp/                    # gapt-mcp — all of GAPT as MCP tools (npm)
├── caddy/                  # Caddy templates
├── poc/                    # M0 PoC artifacts
└── scripts/                # user / admin scripts
```

Full tree: [`docs/plan/00_master_plan.md`](docs/plan/00_master_plan.md) §0.8

---

## Dependencies (primary)

- **`geny-executor` 2.1.0+** (PyPI) — the agent engine
- **Claude Code CLI** (`claude`) — the primary LLM backend
- **Sysbox runc** — container isolation
- **SeaweedFS** — the persistent-file core (host FS is *cache only*)
- **PostgreSQL 16+**, **Redis 7+**, **Caddy 2+**

Full rationale: [`docs/10_tech_stack_decisions.md`](docs/10_tech_stack_decisions.md)

---

## Getting started

### Self-host (M1-E4 — production-ready single VPS)

```bash
git clone https://github.com/CocoRoF/geny-adapted-project-toolkit.git
cd geny-adapted-project-toolkit/compose

# Write `.env` with the required secrets (random 32-char each):
#   GAPT_DOMAIN, GAPT_PREVIEW_DOMAIN, ACME_EMAIL,
#   GAPT_POSTGRES_PASSWORD, GAPT_SESSION_SECRET,
#   GAPT_DAEMON_JWT_SECRET, GAPT_VAULT_MASTER_KEY,
#   GAPT_SHARE_LINK_SECRET, GAPT_SEAWEED_SECRET_KEY
# Full list + .env template in docs/operations/install.md §2.

# Optionally enable Prometheus + Grafana with --profile metrics.
docker compose -f docker-compose.prod.yml --profile metrics up -d
```

Then open `https://<GAPT_DOMAIN>/`, sign in via magic link, and follow
[`docs/operations/install.md`](docs/operations/install.md) §6 to
register GAPT itself as a GAPT project (dogfood).

### Adopting an external repo (Geny case)

[`docs/operations/geny-adapt.md`](docs/operations/geny-adapt.md) walks
through registering Geny, wiring the multi-file compose chain, loading
secrets, and running the first PR cycle from inside GAPT.

### Local dev

```bash
# Boot dependencies (Postgres / Redis / SeaweedFS / Caddy):
docker compose -f compose/docker-compose.dev.yml up -d --wait

# Run the FastAPI server with the local DB:
cd server && uv run uvicorn gapt_server.app:app --reload --port 8001

# Vite dev server on the web UI:
cd web && pnpm dev   # → http://localhost:5173

# Server tests (needs the dev Postgres up).
# IMPORTANT: point at `gapt_test`, NEVER the live `gapt` DB — every
# DSN-gated test runs DROP SCHEMA before the alembic upgrade. The
# guard in `server/tests/_helpers/db_guard.py` will refuse anything
# whose database name doesn't visibly mark it as a test target.
GAPT_TEST_POSTGRES_DSN="postgresql://gapt:gapt_dev_only@localhost:35432/gapt_test" \
  uv run pytest
```

---

## License

[Apache License 2.0](LICENSE).

The core is *permanently OSS*. Cloud add-ons (optional, post-M5) are a separate line but never lock down the OSS core.

---

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md). Every PR must *reference a plan/progress card* — the cadence rules live there.

---

## Related projects

Part of **the Geny ecosystem** — see [The Geny ecosystem](#the-geny-ecosystem) above:
[Geny](https://github.com/CocoRoF/Geny) · [geny-executor](https://github.com/CocoRoF/geny-executor) · [geny-avatar](https://github.com/CocoRoF/geny-avatar)

**Also:**

- [gapt-mcp](mcp/) — npm package exposing all of GAPT as MCP tools (the `mcp/` of this repo)
- [geny-executor](https://github.com/CocoRoF/geny-executor) — the 21-stage agent pipeline GAPT runs on (MIT)
- [Geny](https://github.com/CocoRoF/Geny) — GAPT's *first adapt case* (separate host)
