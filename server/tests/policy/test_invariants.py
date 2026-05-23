"""Unbreakable invariants from §9.2.4."""

from __future__ import annotations

import pytest

from gapt_server.domains.sandbox.backend import MountSpec
from gapt_server.policy import (
    InvariantViolation,
    require_audit_event_actor,
    require_not_agent_session,
    require_owner_id,
    require_safe_sandbox_mount,
    require_secret_not_in_payload,
)


def test_sandbox_mount_invariant_refuses_docker_socket() -> None:
    with pytest.raises(InvariantViolation):
        require_safe_sandbox_mount(
            [MountSpec(source="/var/run/docker.sock", target="/run/docker.sock")]
        )


def test_sandbox_mount_invariant_allows_safe_path() -> None:
    require_safe_sandbox_mount([MountSpec(source="/srv/seaweed/proj-a", target="/workspace")])


def test_owner_id_invariant_rejects_empty() -> None:
    with pytest.raises(InvariantViolation, match="owner_id"):
        require_owner_id("Project", None)
    with pytest.raises(InvariantViolation, match="owner_id"):
        require_owner_id("Project", "")


def test_owner_id_invariant_allows_populated() -> None:
    require_owner_id("Project", "01KS900000000000000000USR1")


@pytest.mark.parametrize(
    "payload",
    [
        {"value": "sk-ant-abcdefghijklmnop"},
        {"nested": {"api_key": "any-non-empty"}},
        "Authorization: ghp_abcdef1234567890abcdef",
        [{"secret": "any"}],
    ],
)
def test_secret_invariant_rejects_obvious_plaintext(payload: object) -> None:
    with pytest.raises(InvariantViolation):
        require_secret_not_in_payload(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "alice", "role": "owner"},
        "Hello, world",
        [{"display_name": "Demo"}],
        {"value": ""},  # empty string skipped
        42,
    ],
)
def test_secret_invariant_allows_safe_values(payload: object) -> None:
    require_secret_not_in_payload(payload)


def test_audit_actor_mismatch_rejected() -> None:
    with pytest.raises(InvariantViolation, match="audit actor mismatch"):
        require_audit_event_actor("01KSXXXUSR1", "01KSXXXUSR2")


def test_audit_actor_match_allowed() -> None:
    require_audit_event_actor("01KSXXXUSR1", "01KSXXXUSR1")
    require_audit_event_actor(None, None)  # system event


def test_agent_cannot_invoke_policy_admin() -> None:
    with pytest.raises(InvariantViolation, match="policy management"):
        require_not_agent_session("agent_session", "policy.override.create")


def test_user_can_invoke_policy_admin() -> None:
    require_not_agent_session("user", "policy.override.create")


def test_non_policy_action_unaffected_for_agent() -> None:
    require_not_agent_session("agent_session", "secret.read")
