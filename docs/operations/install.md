# GAPT self-host install guide

Targets a single VPS (any cloud provider) running Docker 26+ and
Docker Compose v2. The control plane lives in one Docker network;
Caddy fronts everything on :443 with automatic Let's Encrypt
certificates.

## Prerequisites

- A VPS with **public IPv4** reachable on :80 and :443 (Let's Encrypt
  needs HTTP-01 challenges to hit the host).
- A domain you control. You'll need two DNS records:
  - `gapt.example.com  →  <vps-ip>`  (the control plane)
  - `*.preview.gapt.example.com  →  <vps-ip>`  (workspace previews)
- Docker 26+ and Compose v2 (`docker compose version`).
- A Claude API key OR your own LLM-compatible backend (the agent
  backend is configured via the geny-executor manifest — see
  [docs/10_tech_stack_decisions.md](../10_tech_stack_decisions.md)).

## 1 — Clone the repository

```bash
git clone https://github.com/<you>/geny-adapted-project-toolkit.git
cd geny-adapted-project-toolkit
```

## 2 — Write `.env`

Create `compose/.env` next to the compose files. Every secret here is
required at boot — Docker Compose fails fast if any are missing.

```dotenv
# Domains
GAPT_DOMAIN=gapt.example.com
GAPT_PREVIEW_DOMAIN=preview.gapt.example.com
ACME_EMAIL=you@example.com

# Secrets — generate each with `openssl rand -hex 32`
GAPT_POSTGRES_PASSWORD=<random 32+ chars>
GAPT_SESSION_SECRET=<random 32+ chars>
GAPT_DAEMON_JWT_SECRET=<random 32+ chars>
GAPT_VAULT_MASTER_KEY=<random 32+ chars>
GAPT_SHARE_LINK_SECRET=<random 32+ chars>
GAPT_SEAWEED_SECRET_KEY=<random 32+ chars>

# Optional — wire deploy / policy notifications
GAPT_SLACK_WEBHOOK_URL=
GAPT_DISCORD_WEBHOOK_URL=

# Optional — GitHub PAT for /api/projects/:pid/ci/runs (M1 server-wide)
GAPT_CI_GITHUB_TOKEN=

# Optional — server-wide policy YAML
GAPT_POLICY_CONFIG_PATH=

# Optional — Grafana admin password (only needed with --profile metrics)
GRAFANA_ADMIN_PASSWORD=<random 16+ chars>
```

The `GAPT_VAULT_MASTER_KEY` value drives Fernet encryption for the
SecretVault. **Do not rotate it after first boot** without running a
re-encryption migration — every previously-stored secret becomes
unreadable.

## 3 — First boot

```bash
cd compose
docker compose -f docker-compose.prod.yml up -d
```

The first start takes ~30 seconds. Watch for the health checks to
go green:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f caddy server
```

Visit `https://gapt.example.com/`. The first request issues a fresh
Let's Encrypt cert (≤ 30 s); subsequent requests reuse the cached
cert.

## 4 — Magic-link login

Click "Sign in" and enter your email. In dev mode the callback URL
goes to the server log; in prod, wire an SMTP forwarder via your
operator-of-choice tool (the M1 build prints the link to the log —
copy it). Future cycles will plug into a real SMTP provider.

After the first login your account is bootstrapped as **owner** of
its default org.

## 5 — Add the metrics profile (optional)

```bash
docker compose -f docker-compose.prod.yml --profile metrics up -d
```

This adds Prometheus + Grafana behind the same network. Prometheus
scrapes `server:8088/metrics` every 30 s. Grafana ships with a
provisioned "GAPT — overview" dashboard (active sessions, sandbox
counts, cost rate, token throughput). Grafana is not exposed at the
edge by default — bind it behind Caddy on a path you choose, or
expose `127.0.0.1:3000:3000` for tunneled access.

## 6 — Register GAPT as a GAPT project (dogfood)

After login:

1. Click **+ New project** in the UI.
2. Set `slug = gapt`, `display_name = GAPT`,
   `git_remote_url = https://github.com/<you>/geny-adapted-project-toolkit.git`.
3. Create the **dev** environment with `deploy_target_kind = local`
   pointing at the same compose file (or a clone in a separate
   `gapt-dogfood` namespace).
4. Create the **prod** environment with `deploy_target_kind =
   remote_ssh` pointing at this VPS itself. The SSH key needs to be
   loaded into the SecretVault first
   (`POST /api/secrets {key_name: "gapt_prod_ssh_key", ...}`).
5. Open a new workspace from the project page → start a chat → ask
   the agent to read the codebase and make a change. CI runs against
   GitHub Actions, you merge from the GAPT UI, and the deploy panel
   triggers `RemoteSshTarget` against this same VPS.

## 7 — Backup / disaster recovery

The named volumes hold all durable state:

- `postgres-data` — control-plane DB.
- `seaweed-data` — uploaded files + workspace blobs.
- `vault-data` — encrypted secret store. Useless without
  `GAPT_VAULT_MASTER_KEY`.
- `caddy-data` — Let's Encrypt account + issued certs.

A weekly backup of these volumes is enough for full recovery, plus a
copy of `compose/.env` (stored separately — it contains the master
key).

## 8 — Updating GAPT

```bash
git pull
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

The migration runs automatically on server startup
(`alembic upgrade head` inside the container).

## Troubleshooting

- **Caddy can't get a certificate** — check that DNS for
  `gapt.example.com` and `*.preview.gapt.example.com` actually points
  at the VPS. The on-demand TLS hook (Caddyfile.prod) refuses to
  request certs for unknown subdomains; that's by design.
- **`POST /api/notifications/test` succeeds but Slack stays silent**
  — verify `GAPT_SLACK_WEBHOOK_URL` is set and the server saw it via
  `docker compose exec server env | grep SLACK`. The notification
  service swallows webhook errors so the bell still works.
- **`gapt_sessions_active` stays at 0 in Grafana** — the gauge is
  driven by a live DB query; if the metric never moves, ensure
  Prometheus actually reaches the server (`docker compose logs
  prometheus | grep gapt-server`).
