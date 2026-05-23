"""``gapt_read`` — line-windowed file read scoped to workspace_root."""

from __future__ import annotations

from pathlib import Path

from gapt_runtime.tools.protocol import (
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchema,
)
from gapt_runtime.workspace import WorkspaceTraversalError, resolve_under_root

_MAX_LINES_DEFAULT = 2_000
_MAX_FILE_BYTES = 8 * 1_048_576  # 8 MiB hard cap


class GaptRead:
    name = "gapt_read"
    description = (
        "Read a file from the workspace, optionally line-windowed. "
        "Pass `line_offset` (0-based, default 0) and `limit` (default 2000)."
    )
    schema = ToolSchema(
        properties={
            "path": {"type": "string", "description": "Workspace-relative path."},
            "line_offset": {
                "type": "integer",
                "minimum": 0,
                "description": "0-based line index to start at.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "Max number of lines to return (default 2000).",
            },
        },
        required=("path",),
    )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ToolError("exec.tool.invalid_input", "`path` must be a non-empty string")
        line_offset = args.get("line_offset", 0)
        limit = args.get("limit", _MAX_LINES_DEFAULT)
        if not isinstance(line_offset, int) or line_offset < 0:
            raise ToolError("exec.tool.invalid_input", "`line_offset` must be int ≥ 0")
        if not isinstance(limit, int) or limit < 1:
            raise ToolError("exec.tool.invalid_input", "`limit` must be int ≥ 1")

        try:
            target = resolve_under_root(Path(invocation.workspace_root), raw_path)
        except WorkspaceTraversalError as exc:
            raise ToolError("exec.tool.access_denied", str(exc)) from exc
        if not target.exists():
            raise ToolError("exec.tool.invalid_input", f"path not found: {raw_path}")
        if not target.is_file():
            raise ToolError("exec.tool.invalid_input", f"not a regular file: {raw_path}")
        if target.stat().st_size > _MAX_FILE_BYTES:
            raise ToolError(
                "exec.tool.invalid_input",
                f"file is too large for gapt_read ({target.stat().st_size} > {_MAX_FILE_BYTES})",
            )

        text = target.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        windowed = all_lines[line_offset : line_offset + limit]
        return ToolResult(
            content="\n".join(windowed),
            metadata={
                "total_lines": len(all_lines),
                "returned_lines": len(windowed),
                "line_offset": line_offset,
                "truncated": line_offset + limit < len(all_lines),
            },
        )
