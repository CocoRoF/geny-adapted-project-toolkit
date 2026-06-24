"""Orphan classification — a LIVE workspace must never be flagged as orphan
just because its project is archived (the bug that killed Geny's session +
sandbox-tool-pack workspaces).
"""

from __future__ import annotations

from types import SimpleNamespace

from gapt_server.db.enums import WorkspaceStatus
from gapt_server.domains.performance.cleanup import _is_orphan
from gapt_server.domains.performance.sampler import ContainerCategory


def _ws_sample(name: str, workspace_id: str | None, status: str = "running"):
    return SimpleNamespace(
        summary=SimpleNamespace(
            category=ContainerCategory.WORKSPACE,
            name=name,
            workspace_id=workspace_id,
            project_id=None,
            status=status,
        )
    )


def _row(status: WorkspaceStatus, project_id: str):
    return SimpleNamespace(status=status, project_id=project_id)


ARCHIVED_PID = "P_ARCHIVED"


def test_running_workspace_under_archived_project_is_NOT_orphan() -> None:
    # The exact false-positive: live workspace, but its project is archived.
    sample = _ws_sample("gapt-ws-w1", "W1", status="running")
    rows = {"W1": _row(WorkspaceStatus.RUNNING, ARCHIVED_PID)}
    assert _is_orphan(sample, rows, {}, {ARCHIVED_PID}) is False


def test_archived_workspace_row_IS_orphan() -> None:
    sample = _ws_sample("gapt-ws-w2", "W2")
    rows = {"W2": _row(WorkspaceStatus.ARCHIVED, ARCHIVED_PID)}
    assert _is_orphan(sample, rows, {}, {ARCHIVED_PID}) is True


def test_missing_workspace_row_IS_orphan() -> None:
    sample = _ws_sample("gapt-ws-w3", "W3")
    assert _is_orphan(sample, {}, {}, set()) is True


def test_workspace_without_id_label_IS_orphan() -> None:
    sample = _ws_sample("gapt-ws-bad", None)
    assert _is_orphan(sample, {}, {}, set()) is True


def test_exited_agent_sandbox_IS_orphan() -> None:
    # gapt-<id> (not -ws-) that has exited → orphan clutter, remove it.
    sample = _ws_sample("gapt-01ABC", "W4", status="exited")
    rows = {"W4": _row(WorkspaceStatus.RUNNING, "P_LIVE")}
    assert _is_orphan(sample, rows, {}, set()) is True


def test_running_workspace_live_project_is_NOT_orphan() -> None:
    sample = _ws_sample("gapt-ws-w5", "W5", status="running")
    rows = {"W5": _row(WorkspaceStatus.RUNNING, "P_LIVE")}
    assert _is_orphan(sample, rows, {}, set()) is False
