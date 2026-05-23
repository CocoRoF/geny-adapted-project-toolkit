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

    # --- security ---
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
    sandbox_image_tag: str = "ghcr.io/cocorof/gapt-runtime:dev"
    sandbox_daemon_socket: str = "/run/agent.sock"
    sandbox_daemon_token_ttl_s: int = 900  # 15 minutes
    sandbox_idle_pause_s: int = 1800  # 30 minutes → paused
    sandbox_idle_archive_s: int = 86_400  # 24 hours → archive
    # When true, container boots use the real docker SDK + sysbox-runc.
    # Default false keeps unit/CI runs hermetic (MockSandboxBackend).
    sandbox_use_real_docker: bool = False

    # --- arq / background jobs ---
    arq_queue_name: str = "gapt:default"

    # --- audit ---
    audit_flush_interval_s: float = 0.5
    audit_max_batch_size: int = 200

    # --- HTTP trace id ---
    request_id_header: str = "X-Request-Id"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
