"""Unit tests for `validate_target_config` — Phase H.1.

These are pure unit tests; no DB, no FastAPI. The HTTP-layer
integration (422 with `fields` list) is covered separately in
`tests/environments/test_target_config_http.py`.
"""

from __future__ import annotations

import pytest

from gapt_server.db.enums import DeployTargetKind
from gapt_server.domains.environments import (
    KindNotSupportedError,
    TargetConfigInvalidError,
    validate_target_config,
)


# ─────────────────────────────────────────── local ──


def test_local_empty_dict_is_valid() -> None:
    """The modal might submit an empty `deploy_target_config` —
    every field has a deploy-side default, so empty must be valid."""
    out = validate_target_config(DeployTargetKind.LOCAL, {})
    # `exclude_none=True` strips Nones; `compose_paths` defaults to []
    # via the field_validator, which IS not None, so it stays.
    assert out == {"compose_paths": []}


def test_local_typical_values_round_trip() -> None:
    cleaned = validate_target_config(
        DeployTargetKind.LOCAL,
        {
            "compose_path": "docker-compose.prod.yml",
            "preview_mode": "path",
            "preview_slug": "myapp",
            "primary_service": "nginx",
            "primary_port": 8080,
            "upstream_scheme": "https",
            "upstream_tls_insecure": True,
            "strip_prefix": False,
            "build": True,
        },
    )
    assert cleaned["compose_path"] == "docker-compose.prod.yml"
    assert cleaned["preview_mode"] == "path"
    assert cleaned["primary_port"] == 8080
    assert cleaned["upstream_scheme"] == "https"
    assert cleaned["upstream_tls_insecure"] is True


def test_local_port_out_of_range_rejected() -> None:
    with pytest.raises(TargetConfigInvalidError) as exc:
        validate_target_config(DeployTargetKind.LOCAL, {"primary_port": 99999})
    fields = exc.value.fields
    assert any(f["loc"] == ["primary_port"] for f in fields)


def test_local_unknown_scheme_rejected() -> None:
    """`upstream_scheme` is `Literal["http","https"]` — anything
    else (e.g. `ftp`) must produce a field error so the operator
    knows where to look."""
    with pytest.raises(TargetConfigInvalidError) as exc:
        validate_target_config(
            DeployTargetKind.LOCAL, {"upstream_scheme": "ftp"}
        )
    assert any(f["loc"] == ["upstream_scheme"] for f in exc.value.fields)


def test_local_unknown_keys_pass_through() -> None:
    """`extra="allow"` — legacy keys stay so an old row's edit
    doesn't silently drop them. The UI surfaces them in a read-only
    extension panel (Phase H.2)."""
    out = validate_target_config(
        DeployTargetKind.LOCAL,
        {"compose_path": "a.yml", "legacy_thing": "kept-as-is"},
    )
    assert out["legacy_thing"] == "kept-as-is"


def test_local_compose_paths_strips_blanks_and_whitespace() -> None:
    out = validate_target_config(
        DeployTargetKind.LOCAL,
        {"compose_paths": ["  a.yml ", "", " b.yml"]},
    )
    assert out["compose_paths"] == ["a.yml", "b.yml"]


def test_local_preview_slug_length_capped() -> None:
    """DNS label limit; the regex check is the responsibility of the
    deploy router (where the slug actually becomes a DNS label), but
    a 400-char input never makes it into the DB."""
    with pytest.raises(TargetConfigInvalidError):
        validate_target_config(
            DeployTargetKind.LOCAL, {"preview_slug": "x" * 200}
        )


# ───────────────────────────────────── remote_ssh ──


def test_remote_ssh_host_required() -> None:
    with pytest.raises(TargetConfigInvalidError) as exc:
        validate_target_config(DeployTargetKind.REMOTE_SSH, {})
    assert any(f["loc"] == ["host"] for f in exc.value.fields)


def test_remote_ssh_defaults_filled_when_only_host_given() -> None:
    out = validate_target_config(
        DeployTargetKind.REMOTE_SSH, {"host": "srv.example.com"}
    )
    assert out == {
        "host": "srv.example.com",
        "user": "deploy",
        "port": 22,
        "compose_path": "docker-compose.yml",
    }


def test_remote_ssh_port_range_enforced() -> None:
    with pytest.raises(TargetConfigInvalidError):
        validate_target_config(
            DeployTargetKind.REMOTE_SSH, {"host": "h", "port": 0}
        )
    with pytest.raises(TargetConfigInvalidError):
        validate_target_config(
            DeployTargetKind.REMOTE_SSH, {"host": "h", "port": 70000}
        )


# ──────────────────────────────────────── webhook ──


def test_webhook_url_required() -> None:
    with pytest.raises(TargetConfigInvalidError) as exc:
        validate_target_config(DeployTargetKind.WEBHOOK, {})
    assert any(f["loc"] == ["url"] for f in exc.value.fields)


def test_webhook_url_must_parse_as_http() -> None:
    """`not-a-url` should be rejected with a clear field error so the
    modal can red-line the URL input rather than dumping the user
    into a generic 422 with no anchor."""
    with pytest.raises(TargetConfigInvalidError):
        validate_target_config(
            DeployTargetKind.WEBHOOK, {"url": "not-a-url"}
        )


def test_webhook_env_keys_deduped() -> None:
    out = validate_target_config(
        DeployTargetKind.WEBHOOK,
        {
            "url": "https://hook.example/x",
            "env_keys": [" k1 ", "k1", "", "k2"],
        },
    )
    assert out["env_keys"] == ["k1", "k2"]


# ───────────────────────────────────────── k8s ──


def test_k8s_kind_is_explicitly_not_supported() -> None:
    """Phase H surface for the M2 outline's "k8s out of v1 scope"
    decision: instead of silently accepting any dict, we surface a
    distinct error code the UI can route to its own banner."""
    with pytest.raises(KindNotSupportedError):
        validate_target_config(DeployTargetKind.K8S, {})


# ──────────────────────────────── input edge cases ──


def test_none_raw_treated_as_empty_dict() -> None:
    """The router accepts `deploy_target_config: dict | None`; a None
    must validate just like `{}` for kinds whose fields are all
    optional. (remote_ssh / webhook still require their core fields.)"""
    out = validate_target_config(DeployTargetKind.LOCAL, None)
    assert out == {"compose_paths": []}
