"""Workspace snapshots — git-grade, AI-first checkpoints.

See :mod:`gapt_server.domains.snapshots.service` for the capture/restore/diff
logic and ``gapt_server.routers.snapshots`` for the HTTP surface.
"""

from __future__ import annotations

from gapt_server.domains.snapshots.service import (
    SnapshotError,
    capture,
    compute_diff,
    delete,
    get,
    list_for_workspace,
    restore,
)

__all__ = [
    "SnapshotError",
    "capture",
    "compute_diff",
    "delete",
    "get",
    "list_for_workspace",
    "restore",
]
