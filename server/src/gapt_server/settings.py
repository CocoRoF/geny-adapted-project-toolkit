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
    # Wildcard domain — workspaces resolve to
    # `{workspace_slug}.{caddy_preview_domain}/`. Required when
    # `caddy_admin_url` is set.
    caddy_preview_domain: str | None = None
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
