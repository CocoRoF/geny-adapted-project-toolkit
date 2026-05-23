# Adapting GAPT to Geny — operator runbook

This document is the step-by-step companion to
[docs/12_geny_case_study.md](../12_geny_case_study.md) §12.3. The case
study explains *what* should happen; this runbook is the *how*, with
the exact endpoints / fields / pitfalls a single operator runs through
the first time they bring Geny under GAPT.

Successful completion means the operator merged at least one Geny
PR through GAPT without opening their desktop IDE — that's the M1-E4
DoD "Geny first adapt".

> **Prerequisites**: GAPT is already running per
> [install.md](install.md) and the user is signed in.
> Source repository: `https://github.com/CocoRoF/Geny`.

## 1 — Register the Geny project

Either via the UI (**+ New project**) or `POST /api/projects`:

```bash
curl -b /tmp/cookies.txt -X POST http://localhost:8001/api/projects \
  -H "Content-Type: application/json" \
  -d '{
    "org_id": "<your-org-ulid>",
    "slug": "geny",
    "display_name": "Geny",
    "git_remote_url": "https://github.com/CocoRoF/Geny.git",
    "compose_profile_dev": "dev",
    "compose_profile_prod": "prod"
  }'
```

The project row carries `default_compose_paths` — leave it empty;
each environment supplies its own chain (next step).

## 2 — Create the dev + prod environments

Geny needs **two compose files chained per environment**
(`dev.yml` + `dev-core.yml`, `prod.yml` + `prod-core.yml`). GAPT
supports this via the `compose_paths` field on
`deploy_target_config`:

```bash
# dev: local same-host docker
curl -b /tmp/cookies.txt -X POST \
  http://localhost:8001/api/projects/{pid}/environments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dev",
    "deploy_target_kind": "local",
    "deploy_target_config": {
      "compose_paths": [
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.dev-core.yml"
      ]
    },
    "require_2fa": false,
    "secret_refs": ["geny.env.dev"]
  }'

# prod: remote SSH on the user's VPS
curl -b /tmp/cookies.txt -X POST \
  http://localhost:8001/api/projects/{pid}/environments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod",
    "deploy_target_kind": "remote_ssh",
    "deploy_target_config": {
      "compose_paths": [
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.prod-core.yml"
      ],
      "host": "geny-prod.yourdomain.com",
      "user": "deploy"
    },
    "require_2fa": true,
    "secret_refs": ["geny.env.prod", "geny.prod.ssh_key"]
  }'
```

Notes:

- `compose_paths` overrides the legacy `compose_path` (`-f a -f b ...`
  chain in argv order — see
  [server/src/gapt_server/domains/deploy/local.py](../../server/src/gapt_server/domains/deploy/local.py#L59)).
- `require_2fa=true` on prod combined with the `INVARIANT_FLOORS` in
  `policy/config_loader.py` means a prod deploy can never be relaxed
  below 2FA, even if a server-wide YAML tries to ALLOW it.

## 3 — Load secrets into the vault

```bash
# .env.dev as a single base64-encoded file (or as discrete keys —
# both shapes work, the SecretBackend stores them as-is).
curl -b /tmp/cookies.txt -X POST \
  http://localhost:8001/api/secrets \
  -H "Content-Type: application/json" \
  -d '{
    "owner_scope": "project",
    "owner_id": "{pid}",
    "key_name": "geny.env.dev",
    "backend": "encrypted_sqlite",
    "value": "<base64 of .env.dev>"
  }'

# SSH key for prod deploy — single PEM, never written to host disk.
curl -b /tmp/cookies.txt -X POST \
  http://localhost:8001/api/secrets \
  -H "Content-Type: application/json" \
  -d '{
    "owner_scope": "project",
    "owner_id": "{pid}",
    "key_name": "geny.prod.ssh_key",
    "backend": "encrypted_sqlite",
    "value": "<base64 of id_ed25519 PEM>"
  }'
```

## 4 — Create the workspace + boot the sandbox

From the UI, open the Geny project → **+ Workspace** → `main`. Behind
the scenes:

- A Sysbox container boots (image
  `ghcr.io/cocorof/gapt-runtime:dev` per
  `GAPT_SANDBOX_IMAGE_TAG`).
- The agent daemon clones `https://github.com/CocoRoF/Geny.git` into
  `/workspace/main`.
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml -f
  docker-compose.dev-core.yml up -d` runs *inside* the sandbox (the
  sandbox owns its own dockerd — the host's socket is never
  mounted; see [feedback_sudo_compose_home_pitfall](../../06_isolation_and_runtime.md#65)).
- Caddy registers a preview subdomain
  (`<workspace_id>.preview.{GAPT_PREVIEW_DOMAIN}`) automatically
  through `POST /api/workspaces/{wid}/preview`.

If clone time is a problem on the first run (Geny's `vendor/` is
large), pass `?clone_filter=blob:none` in the workspace-create call
(workspace endpoint accepts the param — see
[workspaces router](../../server/src/gapt_server/routers/workspaces.py)).

## 5 — Seed the project memory

In the chat panel, **the first message** should hand the agent a
domain primer. Copy the project's CLAUDE.md (Geny ships one) and
prepend a one-paragraph context. This goes straight into the
session's S18 memory and survives across future workspaces.

Example seed prompt:

> "Project context: this is Geny, a VTuber + Tamagotchi platform.
> Backend = FastAPI, frontend = TS/React, executor = geny-executor
> 2.1.0. The `backend/service/executor/` directory is sacred — don't
> rewrite, only patch. Cycle docs live in `analysis/` `plan/`
> `progress/`. Don't touch `vendor/` (third-party). Read
> `CLAUDE.md` for full conventions."

## 6 — Run a real cycle (the DoD test)

Pick a small, well-scoped cycle from Geny's open backlog. Open a
new chat in the workspace and ask the agent to:

1. Read the relevant cycle's `analysis/` + `plan/` files.
2. Implement the change with `gapt_edit` (diff card review).
3. Add or extend tests.
4. Run pytest (`gapt_bash`).
5. `gapt_git commit`, `gapt_git push -u origin <branch>`,
   `gapt_gh pr create`.
6. Wait for CI green (CI panel polls — refresh manually).
7. Merge via the GitHub UI (or `gh pr merge` from a workspace shell).
8. Deploy to dev: **Deploy → dev → version=main**. Confirm the
   `notifications` panel surfaces a `deploy.success` event (Slack
   too, if `GAPT_SLACK_WEBHOOK_URL` is set).
9. Deploy to prod: **Deploy → prod → 2FA code → version=main**. The
   `INVARIANT_FLOORS` floor will reject any attempt without 2FA.

After step 9, the dogfood criterion is met. Record the run in
Geny's own `progress/` folder.

## 7 — Known traps (from §12.5)

- **Sudo HOME pitfall** — Geny's prod compose may use `${HOME}`.
  Geny's compose was already audited to use absolute paths
  (`/apps/geny/...`); double-check before the first prod deploy.
- **Multi-file compose chains** — pass them via `compose_paths`, NOT
  via a comma-separated `compose_path`. The argv chain is
  `-f a -f b -f c`, in order.
- **vendor/ size** — first clone is slow. Re-uses the same git
  cache on subsequent workspace creates.
- **agent_session_manager.py (~1500 lines)** — let the agent fetch
  it in line-range chunks via `gapt_read`. Wholesale `Read` calls
  blow the context budget.
- **Built-in `Bash` vs `gapt_bash`** — the Claude Code CLI built-in
  `Bash` bypasses the GAPT MCP gate (no audit, no PolicyEngine).
  Add `"Bash(git status)"`, `"Bash(pytest *)"` to the manifest's
  `permissions.allow` and reject bare `Bash`. See §12.5 G-8.

## 8 — After-action review

After the first PR ships through GAPT, create the lessons file:

```
analysis/20260601_geny_first_adapt_lessons.md
```

Capture:
- What worked first try.
- What needed user intervention (tool, prompt, manifest).
- Total cost vs daily cap.
- Whether the agent reached for the desktop IDE (it shouldn't).

This file plugs back into the Geny case study as evidence that the
M1-E4 DoD is met.
