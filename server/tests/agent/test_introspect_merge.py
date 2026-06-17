"""Unit tests for `merge_deploy_config` — the non-destructive
apply-introspection merge that fixes the "re-running the wizard reset my
deploy/preview settings" bug.

The wizard could auto-open on every reconnect; an accidental re-approve
used to overwrite the saved Environment.deploy_target_config (notably
flipping a hand-set `preview_mode: subdomain` back to the detector's
`path`). The merge now refreshes only detected compose facts and never
clobbers user-owned routing fields.
"""

from __future__ import annotations

from gapt_server.routers.introspect import merge_deploy_config

# What the detector contributes — only compose facts, never routing.
_DETECTION = {
    "compose_path": "/w/docker-compose.yml",
    "compose_paths": ["/w/docker-compose.yml"],
    "build": True,
    "primary_service": "frontend",
    "primary_port": 3000,
}


def test_fresh_env_seeds_default_preview_mode() -> None:
    cfg = merge_deploy_config(None, _DETECTION, None)
    assert cfg["preview_mode"] == "path"
    assert cfg["compose_path"] == "/w/docker-compose.yml"
    assert cfg["primary_port"] == 3000


def test_fresh_env_honours_explicit_preview_mode() -> None:
    cfg = merge_deploy_config(None, _DETECTION, "subdomain")
    assert cfg["preview_mode"] == "subdomain"


def test_existing_env_preserves_user_routing_on_reapply() -> None:
    """The crux: a user set subdomain mode + slug + strip_prefix; a
    re-run with no explicit preview_mode must leave ALL of it intact and
    only refresh the compose facts."""
    existing = {
        "compose_path": "/old.yml",
        "compose_paths": ["/old.yml"],
        "build": False,
        "preview_mode": "subdomain",
        "preview_slug": "my-test",
        "strip_prefix": False,
        "upstream_scheme": "https",
        "upstream_tls_insecure": True,
    }
    cfg = merge_deploy_config(existing, _DETECTION, None)
    # User routing untouched.
    assert cfg["preview_mode"] == "subdomain"
    assert cfg["preview_slug"] == "my-test"
    assert cfg["strip_prefix"] is False
    assert cfg["upstream_scheme"] == "https"
    assert cfg["upstream_tls_insecure"] is True
    # Compose facts refreshed.
    assert cfg["compose_path"] == "/w/docker-compose.yml"
    assert cfg["build"] is True
    assert cfg["primary_service"] == "frontend"


def test_existing_env_explicit_preview_mode_still_applies() -> None:
    """An explicit choice in the request DOES apply — the user actively
    picked it this run."""
    existing = {"preview_mode": "subdomain", "preview_slug": "keep"}
    cfg = merge_deploy_config(existing, _DETECTION, "path")
    assert cfg["preview_mode"] == "path"
    # Other user fields still preserved.
    assert cfg["preview_slug"] == "keep"


def test_existing_none_config_is_handled() -> None:
    # An env row with an empty/None config behaves like an existing env
    # (no crash); detection facts land, no preview_mode forced.
    cfg = merge_deploy_config({}, _DETECTION, None)
    assert cfg["compose_path"] == "/w/docker-compose.yml"
    assert "preview_mode" not in cfg  # not forced on a non-None existing
