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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
