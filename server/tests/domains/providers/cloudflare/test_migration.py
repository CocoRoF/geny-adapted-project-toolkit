"""Local-cloudflared → remote-managed migration helpers."""

from __future__ import annotations

import os
import re

import pytest

from gapt_server.domains.providers.cloudflare.migration import (
    LocalConfigError,
    UnsafeTunnelIdError,
    _ensure_safe_tunnel_id,
    extract_tunnel_uuid,
    generate_cutover_script,
    generate_revert_script,
    inspect_local,
    looks_like_uuid,
)


# ────────────────────────────────────────────── UUID helpers ──


class TestUuidHelpers:
    def test_looks_like_uuid_canonical(self) -> None:
        assert looks_like_uuid("802da1da-bd39-42d6-ad84-1c865f9f57bd")

    def test_looks_like_uuid_friendly_name_rejected(self) -> None:
        assert not looks_like_uuid("hr109")

    def test_extract_from_uuid_field(self) -> None:
        assert (
            extract_tunnel_uuid("802da1da-bd39-42d6-ad84-1c865f9f57bd", None)
            == "802da1da-bd39-42d6-ad84-1c865f9f57bd"
        )

    def test_extract_from_credentials_file_when_name_is_friendly(self) -> None:
        """The user's actual case during Phase B: `tunnel: hr109`
        in config.yml, but the credentials file path embeds the
        real UUID. Cloudflare API needs the UUID."""
        out = extract_tunnel_uuid(
            "hr109",
            "/etc/cloudflared/802da1da-bd39-42d6-ad84-1c865f9f57bd.json",
        )
        assert out == "802da1da-bd39-42d6-ad84-1c865f9f57bd"

    def test_extract_returns_none_when_no_source(self) -> None:
        assert extract_tunnel_uuid("hr109", None) is None
        assert extract_tunnel_uuid(None, None) is None


# ────────────────────────────────────────── safety regex ──


class TestEnsureSafeTunnelId:
    """`_ensure_safe_tunnel_id` is the last line of defense before
    a value lands in a shell script that runs as root. Anything
    that's not strict alnum/hyphen must be rejected."""

    @pytest.mark.parametrize(
        "good",
        [
            "802da1da-bd39-42d6-ad84-1c865f9f57bd",
            "hr109",
            "a",
            "ABC-123",
        ],
    )
    def test_accepts_well_formed(self, good: str) -> None:
        assert _ensure_safe_tunnel_id(good) == good

    @pytest.mark.parametrize(
        "evil",
        [
            "'; rm -rf / #",
            "$(curl evil)",
            "tunnel; reboot",
            "a b",
            "a/b",
            "../../etc/passwd",
            "",
            "a" * 65,  # > 64 chars
        ],
    )
    def test_rejects_unsafe(self, evil: str) -> None:
        with pytest.raises(UnsafeTunnelIdError):
            _ensure_safe_tunnel_id(evil)


# ─────────────────────────────────── script generation ──


class TestCutoverScript:
    def test_embeds_validated_tunnel_id(self) -> None:
        script = generate_cutover_script("802da1da-bd39-42d6-ad84-1c865f9f57bd")
        assert "TUNNEL_ID=802da1da-bd39-42d6-ad84-1c865f9f57bd" in script
        # Requires root.
        assert 'id -u' in script and "$" in script
        # Drops the systemd override.
        assert "gapt-remote-managed.conf" in script
        # Restarts cloudflared.
        assert "systemctl restart cloudflared.service" in script

    def test_friendly_name_is_also_accepted(self) -> None:
        # cloudflared accepts both UUID and friendly name in `tunnel run`,
        # so the regex allows both. The Cloudflare API push uses the
        # UUID separately.
        script = generate_cutover_script("hr109")
        assert "TUNNEL_ID=hr109" in script

    def test_rejects_injection_attempt(self) -> None:
        with pytest.raises(UnsafeTunnelIdError):
            generate_cutover_script("'; rm -rf /; #")

    def test_revert_script_shape(self) -> None:
        script = generate_revert_script()
        assert "rm -f" in script
        assert "gapt-remote-managed.conf" in script
        assert "systemctl restart cloudflared.service" in script


# ─────────────────────────────────── inspect_local ──


class TestInspectLocal:
    def test_returns_not_exists_when_missing(self, tmp_path, monkeypatch) -> None:
        bogus = tmp_path / "nonexistent.yml"
        monkeypatch.setenv("GAPT_CLOUDFLARED_CONFIG_PATH", str(bogus))
        result = inspect_local()
        assert result.exists is False
        assert result.readable is False
        assert result.tunnel_id is None
        assert result.tunnel_uuid is None
        assert result.ingress == []

    def test_parses_real_world_config(self, tmp_path, monkeypatch) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text(
            """tunnel: hr109
credentials-file: /etc/cloudflared/802da1da-bd39-42d6-ad84-1c865f9f57bd.json

ingress:
  - hostname: gapt.example.com
    service: http://localhost:38080
    originRequest:
      noTLSVerify: true
  - hostname: '*.gapt.example.com'
    service: http://localhost:38080
  - service: http_status:404
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("GAPT_CLOUDFLARED_CONFIG_PATH", str(cfg))
        result = inspect_local()
        assert result.exists and result.readable
        assert result.tunnel_id == "hr109"
        assert result.tunnel_uuid == "802da1da-bd39-42d6-ad84-1c865f9f57bd"
        assert result.credentials_file.endswith(
            "802da1da-bd39-42d6-ad84-1c865f9f57bd.json"
        )
        assert len(result.ingress) == 3
        # originRequest stripped through the normaliser keeps the
        # camelCase key Cloudflare's API expects.
        assert result.ingress[0].get("originRequest") == {"noTLSVerify": True}

    def test_invalid_yaml_raises(self, tmp_path, monkeypatch) -> None:
        bad = tmp_path / "config.yml"
        bad.write_text("tunnel: x\n  bad: indent\n yes:\n", encoding="utf-8")
        monkeypatch.setenv("GAPT_CLOUDFLARED_CONFIG_PATH", str(bad))
        with pytest.raises(LocalConfigError) as ei:
            inspect_local()
        assert "valid YAML" in str(ei.value)

    def test_ingress_not_list_raises(self, tmp_path, monkeypatch) -> None:
        bad = tmp_path / "config.yml"
        bad.write_text("tunnel: x\ningress: oops\n", encoding="utf-8")
        monkeypatch.setenv("GAPT_CLOUDFLARED_CONFIG_PATH", str(bad))
        with pytest.raises(LocalConfigError) as ei:
            inspect_local()
        assert "ingress" in str(ei.value)
