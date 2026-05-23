"""Daemon-side tool implementations.

Four core tools shipped in M1-E2 Cycle 2.4:

- ``gapt_read``  — read a file (with optional line offset / limit).
- ``gapt_glob``  — recursive filename match against a glob pattern.
- ``gapt_grep``  — regex search inside files under the workspace.
- ``gapt_edit``  — find-and-replace inside a single file.

Every tool runs inside the sandbox daemon. Paths are scoped to
``workspace_root`` by the same ``_resolve_under_root`` helper the rest
of the daemon uses — there's no path-traversal escape, no symlink-jump
into the host. ``gapt_edit`` is the only mutating tool; the rest are
read-only.
"""

from gapt_runtime.tools.edit import GaptEdit
from gapt_runtime.tools.git_tool import GaptGit
from gapt_runtime.tools.glob import GaptGlob
from gapt_runtime.tools.grep import GaptGrep
from gapt_runtime.tools.pr_tool import GaptPr
from gapt_runtime.tools.protocol import (
    Tool,
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchema,
)
from gapt_runtime.tools.read import GaptRead
from gapt_runtime.tools.registry import ToolRegistry, build_default_registry

__all__ = [
    "GaptEdit",
    "GaptGit",
    "GaptGlob",
    "GaptGrep",
    "GaptPr",
    "GaptRead",
    "Tool",
    "ToolError",
    "ToolInvocation",
    "ToolRegistry",
    "ToolResult",
    "ToolSchema",
    "build_default_registry",
]
