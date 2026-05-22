import pytest

from gapt_server.settings import Settings, get_settings


def test_settings_defaults() -> None:
    settings = Settings(session_secret="x", daemon_jwt_secret="y")

    assert settings.env == "dev"
    assert settings.port == 8080
    assert settings.seaweed_bucket == "gapt"
    assert settings.default_manifest_id == "gapt_default"
    assert settings.claude_binary_path == "/usr/local/bin/claude"
    assert settings.cors_origins == []


def test_settings_env_prefix_picks_up_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAPT_PORT", "9090")
    monkeypatch.setenv("GAPT_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GAPT_SEAWEED_BUCKET", "custom")
    monkeypatch.setenv("GAPT_SESSION_SECRET", "from-env")
    monkeypatch.setenv("GAPT_DAEMON_JWT_SECRET", "from-env")

    settings = Settings()

    assert settings.port == 9090
    assert settings.log_level == "DEBUG"
    assert settings.seaweed_bucket == "custom"


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()

    assert a is b
