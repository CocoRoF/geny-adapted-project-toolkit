"""YAML loader + invariant enforcement."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from gapt_server.policy.config_loader import (
    INVARIANT_FLOORS,
    PolicyConfigError,
    check_invariant,
    load_yaml,
    parse_dict,
)
from gapt_server.policy.engine import PolicyDecision


def test_load_missing_path_returns_empty_set(tmp_path: Path) -> None:
    result = load_yaml(tmp_path / "missing.yaml")
    assert result.by_action == {}


def test_parse_dict_short_form(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent(
        """
        actions:
          deploy.prod: deny
          git.push.protected: require_user_approval
        """
    ).strip()
    f = tmp_path / "policy.yaml"
    f.write_text(yaml_text)

    result = load_yaml(f)

    assert result.by_action["deploy.prod"].decision is PolicyDecision.DENY
    assert (
        result.by_action["git.push.protected"].decision
        is PolicyDecision.REQUIRE_USER_APPROVAL
    )
    assert result.by_action["deploy.prod"].source_layer == "server"


def test_parse_dict_long_form_with_reason(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent(
        """
        actions:
          deploy.dev:
            decision: allow
            reason: dev push is the inner-loop fast path
        """
    ).strip()
    f = tmp_path / "policy.yaml"
    f.write_text(yaml_text)
    result = load_yaml(f)
    override = result.by_action["deploy.dev"]
    assert override.decision is PolicyDecision.ALLOW
    assert override.reason == "dev push is the inner-loop fast path"


def test_invariant_floor_blocks_relaxation() -> None:
    # deploy.prod's floor is REQUIRE_2FA — allowing it should raise.
    with pytest.raises(PolicyConfigError) as exc:
        check_invariant("deploy.prod", PolicyDecision.ALLOW)
    assert exc.value.code == "policy.config.invariant_violated"

    # REQUIRE_2FA itself is fine.
    check_invariant("deploy.prod", PolicyDecision.REQUIRE_2FA)

    # DENY (stricter) is also fine.
    check_invariant("deploy.prod", PolicyDecision.DENY)


def test_invariant_floor_blocks_yaml_that_relaxes() -> None:
    raw = {"actions": {"git.push.force": "allow"}}
    with pytest.raises(PolicyConfigError) as exc:
        parse_dict(raw, source_layer="server")
    assert exc.value.code == "policy.config.invariant_violated"


def test_unknown_decision_string() -> None:
    raw = {"actions": {"deploy.dev": "sometimes"}}
    with pytest.raises(PolicyConfigError) as exc:
        parse_dict(raw, source_layer="server")
    assert exc.value.code == "policy.config.bad_decision"


def test_invariant_floors_cover_critical_actions() -> None:
    expected = {"deploy.prod", "secret.create", "secret.update", "secret.delete", "git.push.force"}
    assert expected <= set(INVARIANT_FLOORS.keys())
