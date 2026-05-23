"""Workspaces domain.

A `Workspace` is the bridge between a `Project` and a running sandbox
container. Cycle 1.8 wires the CRUD shell + sandbox launch / teardown.
The richer agent-session + SeaweedFS-mount integration lands in
M1-E2.
"""

from gapt_server.domains.workspaces.service import (
    WorkspaceError,
    WorkspaceService,
    WorkspaceView,
)

__all__ = ["WorkspaceError", "WorkspaceService", "WorkspaceView"]
