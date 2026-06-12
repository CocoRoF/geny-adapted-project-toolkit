from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GAPT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- meta ---
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- HTTP ---
    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = Field(default_factory=list)

    # --- storage (M0-P1 PR2 stub — DSN 미설정 시 disabled) ---
    postgres_dsn: PostgresDsn | None = None
    redis_dsn: RedisDsn | None = None

    # --- SeaweedFS (영속 파일 코어 — host FS는 cache only) ---
    seaweed_filer_url: str | None = None  # e.g. http://seaweed-filer:8888
    seaweed_s3_endpoint: str | None = None  # e.g. http://seaweed-s3:8333
    seaweed_s3_access_key: str | None = None
    seaweed_s3_secret_key: str | None = None
    seaweed_bucket: str = "gapt"

    # --- agent / LLM ---
    claude_binary_path: str = "/usr/local/bin/claude"
    default_manifest_id: str = "gapt_default"

    # --- security / admin auth ---
    # MinIO/Jenkins-style single-admin auth. There is no multi-user
    # account system — GAPT is a self-hosted solo tool. Set these env
    # vars to lock the instance; defaults are admin/admin so a fresh
    # boot is one click away.
    admin_id: str = "admin"
    admin_password: str = "admin"
    # When false, every request is treated as the admin without
    # requiring a login. Use for trusted localhost-only deployments
    # where the cookie dance is just friction. Default true keeps the
    # login screen in place.
    auth_enabled: bool = True
    session_cookie_name: str = "gapt_session"
    session_secret: str = "dev-only-secret-change-me"
    daemon_jwt_secret: str = "dev-only-daemon-secret-change-me"
    # Master key for the local secret vault. PBKDF2-derived → Fernet
    # 32-byte key. Override in prod via env; *never* commit a real value.
    vault_master_key: str = "dev-only-vault-master-change-me"
    # Where the EncryptedSqliteBackend stores its ciphertext blobs.
    # Plaintext NEVER lands in Postgres — only the SecretRef pointer.
    vault_sqlite_path: str = ".gapt/local/vault.sqlite3"

    # --- sandbox / runtime (Cycle 1.7 onwards) ---
    sandbox_runtime: str = "sysbox-runc"
    # Per-workspace sandbox image — built locally via
    # `docker/workspace/build.sh` (tag `gapt-workspace:latest`). We
    # ship no pushed registry image yet, so the default has to be
    # something the operator actually has on disk after running the
    # build script. Override with `GAPT_SANDBOX_IMAGE_TAG=...` once a
    # registry copy exists.
    sandbox_image_tag: str = "gapt-workspace:latest"
    sandbox_daemon_socket: str = "/run/agent.sock"
    sandbox_daemon_token_ttl_s: int = 900  # 15 minutes
    sandbox_idle_pause_s: int = 1800  # 30 minutes → paused
    sandbox_idle_archive_s: int = 86_400  # 24 hours → archive
    # Phase C.2.d — cap on concurrently-live workspaces to keep host
    # resources bounded. Counts rows with status IN (CREATING, RUNNING).
    # Stopped/paused/failed/archived are excluded since they don't hold
    # an active container. Override via `GAPT_MAX_ACTIVE_SANDBOXES`.
    max_active_sandboxes: int = 6

    # Phase E.1 — GPU policy for workspace sandboxes (`gapt-ws-<wid>`
    # containers, NOT the agent sandbox `gapt-<id>` series). Passed
    # through to `docker run --gpus <value>`. Recognised values:
    #   None     — no GPU (default; CPU-only)
    #   "all"    — map every host GPU
    #   "0"      — single device by index
    #   "0,1"    — multiple devices, comma-separated
    # Single-admin scope: one policy for the install. Per-workspace
    # override is deferred to v1.5 (real demand will tell us if it's
    # worth a UI). Requires NVIDIA Container Toolkit on the host —
    # `docker run` fails loud when the toolkit is missing rather than
    # silently giving the agent a CPU-only container.
    workspace_gpus: str | None = None

    # Where bare repos live on the host. Each project gets a
    # `<workspace_bare_root>/<project_slug>/` subdirectory holding the
    # `git clone --bare` of its remote. The workspace's `.git` file
    # references that absolute path, and the workspace container
    # mounts the same path inside so commits resolve.
    #
    # MUST be outside `/workspace` (and any other path mounted as a
    # workspace worktree) — otherwise the bare's parent directories
    # show up as untracked entries inside the worktree (the bug that
    # caused this setting to exist). Default `/var/lib/gapt-bare`
    # is system-style and cleanly disjoint from typical workspace
    # roots. Override via `GAPT_WORKSPACE_BARE_ROOT` when running on
    # a host where `/var/lib` isn't writable by the GAPT user.
    workspace_bare_root: str = "/var/lib/gapt-bare"

    # ── Phase M.1 — agent session memory bounds ──────────────────
    #
    # All five knobs are exposed as `GAPT_SESSION_*` env vars so an
    # operator running on a small VPS can drop the caps without
    # patching code. Defaults match the deep-review's "recommended
    # safe baseline" — adequate for a single-admin solo install with
    # ~100 active sessions across all workspaces.
    #
    # Bumping any of these unconditionally raises the worst-case
    # memory ceiling — start small and only raise after observing
    # actual pressure in `performance` metrics.

    # Max `SessionRuntime` instances the in-process `SessionRegistry`
    # holds at once. Touching a session (invoke / stream / messages)
    # bumps it to the front of an LRU queue; when this cap is hit a
    # `register()` evicts the least-recently-touched runtime via
    # `aclose()` — its `conversation_state` + bus subscribers are
    # released. Subsequent activity on that session triggers a normal
    # rehydrate from DB (Phase L.1 contract).
    session_runtime_cache_size: int = 50

    # Wall-clock idle window (seconds) after which a runtime is
    # evicted by the background sweep even if the LRU cap hasn't been
    # reached. Idle = no SSE/invoke/touch in this many seconds. The
    # default is conservative (30 min) so a user coming back from
    # lunch still hits the warm cache; an active chat session bumps
    # `last_active_at` on every event so it never starves.
    session_runtime_idle_eviction_s: int = 1800

    # Upper bound on the `session_events` rows the rehydrate path
    # loads to reconstruct `state.messages`. Older events are
    # dropped — the agent loses memory of the very-oldest turns but
    # the latest N stay intact. Picked to cap a worst-case session
    # at ~100 KB of message JSON.
    session_max_rehydrate_events: int = 1000

    # Hard cap on `state.messages` entries (one entry = one role
    # turn — so 50 entries ≈ 25 user/assistant pairs). When the
    # invoke driver sees a longer array it trims the head so the
    # next `Pipeline.run_stream` doesn't push the context window.
    # Operator can grow this for opus / haiku 200k contexts.
    session_max_messages_in_state: int = 50

    # SSE replay (`_full_replay`) cap. Larger than the rehydrate
    # cap because replay also covers UI step / cost / tool events,
    # which are noisier than the canonical user/assistant pairs the
    # agent needs for memory.
    session_max_stream_replay_events: int = 2000

    # --- arq / background jobs ---
    arq_queue_name: str = "gapt:default"

    # --- audit ---
    audit_flush_interval_s: float = 0.5
    audit_max_batch_size: int = 200

    # --- HTTP trace id ---
    request_id_header: str = "X-Request-Id"

    # --- GitHub OAuth Device Flow (Cycle 2.5) ---
    # Operator must register a GitHub OAuth App and set the client id
    # here. client_secret is only needed for `revoke` and is stored in
    # the secret vault under the "system" scope.
    github_oauth_client_id: str | None = None
    github_oauth_secret_key: str = "github_oauth_client_secret"
    github_oauth_scopes: str = "repo,workflow"

    # --- CI surface (Cycle 4.3) ---
    # Server-wide GitHub token used by the CI runs endpoint. In M1 this
    # is the operator-supplied dev token; later cycles look up the
    # token per project from Secret Vault using
    # `projects.git_auth_secret_ref`.
    ci_github_token: str | None = None

    # --- Caddy preview subdomain (Cycle 4.4) ---
    # Caddy admin API URL (typically http://caddy:2019). When unset,
    # preview-subdomain endpoints return 412 `preview.disabled` so the
    # UI can render a "configure Caddy" hint.
    caddy_admin_url: str | None = None
    # Base URL at which WORKSPACE CONTAINERS can reach the GAPT API —
    # used to point the in-sandbox agent's MCP client at the
    # self-introspection endpoint (`<base>/_gapt/api/mcp/mcp`). The
    # containers share gapt-net with Caddy, so the Caddy container
    # address is the natural value (e.g. `http://gapt-dev-caddy-1:8080`).
    # None disables agent self-introspection entirely.
    agent_self_mcp_base_url: str | None = None
    # Wildcard domain — workspaces resolve to
    # `{workspace_slug}.{caddy_preview_domain}/`. Required when
    # `caddy_admin_url` is set.
    caddy_preview_domain: str | None = None
    # Public host that serves the GAPT IDE itself (e.g.
    # `gapt.hrletsgo.me`). Used by SubdomainManager to add a
    # zone-wide catch-all 404 for unregistered subdomains WHILE
    # excluding this host (so visiting GAPT itself still works).
    # Only relevant when `caddy_preview_domain` equals or is a
    # parent of `caddy_apex_host`. Optional — when unset, the
    # catch-all is registered for `*.<preview-domain>` without an
    # exclusion (safe when preview_domain is a strict sub-host
    # of the GAPT apex, like `previews.gapt.example`).
    caddy_apex_host: str | None = None
    # Wildcard-cert zone for **subdomain-mode** previews. Distinct
    # from `caddy_preview_domain` because the two answer different
    # questions:
    #   * `caddy_preview_domain` — host that serves the path-mode
    #     `/preview/<slug>` route family. Typically the GAPT IDE
    #     host itself (e.g. `gapt.hrletsgo.me`) so previews and IDE
    #     share one cert.
    #   * `caddy_subdomain_zone` — the parent zone whose wildcard
    #     cert (`*.<zone>`) terminates subdomain-mode hosts like
    #     `hr-test.<zone>`. On Cloudflare's free plan only the
    #     one-level wildcard `*.<root-zone>` exists, so when GAPT
    #     lives at a sub-host (e.g. `gapt.hrletsgo.me`) subdomain
    #     mode MUST use the bare root (`hrletsgo.me`) here — using
    #     `caddy_preview_domain` would build `<slug>.gapt.<root>`
    #     which `*.<root>` doesn't cover → ERR_SSL_VERSION.
    # Unset → subdomain mode falls back to `caddy_preview_domain`
    # for full back-compat with single-host installs.
    caddy_subdomain_zone: str | None = None
    # Share link HMAC secret. Do not leave the dev default in prod.
    share_link_secret: str = "dev-only-share-secret-change-me"
    # TTL ceiling for share links (seconds). Default 24h.
    share_link_max_ttl_s: int = 24 * 3600

    # --- Notification webhooks (Cycle 4.8) ---
    # Operator-set incoming webhook URLs. When unset, only the
    # in-memory bell receives notifications. Per-user subscriptions
    # land with a settings UI later.
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None

    # --- PolicyEngine config (Cycle 4.5) ---
    # Path to the server-wide policy YAML (L2). When unset or missing
    # the built-in defaults (L1) apply. The loader enforces the
    # invariant floors (deploy.prod / secret.* / git.push.force) at
    # parse time so a misconfigured file never loosens the gates.
    policy_config_path: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
