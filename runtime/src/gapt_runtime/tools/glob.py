"""``gapt_glob`` — recursive filename match via ``pathlib.glob``.

The runtime image deliberately doesn't pull in ``rg``/``fd`` as
Python deps (they ship as system binaries instead). For M1, Python's
own glob is fast enough on a workspace-sized tree.
"""

from __future__ import annotations

from pathlib import Path

from gapt_runtime.tools.protocol import (
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchema,
)
from gapt_runtime.workspace import WorkspaceTraversalError, resolve_under_root

_MAX_RESULTS = 5_000


class GaptGlob:
    name = "gapt_glob"
    description = (
        "Recursively match files in the workspace against a glob pattern. "
        "Pattern is interpreted relative to the workspace root."
    )
    schema = ToolSchema(
        properties={
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. `src/**/*.py`.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "Cap the number of results (default 5000).",
            },
        },
        required=("pattern",),
    )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("exec.tool.invalid_input", "`pattern` must be a non-empty string")
        limit_raw = args.get("limit", _MAX_RESULTS)
        if not isinstance(limit_raw, int) or limit_raw < 1:
            raise ToolError("exec.tool.invalid_input", "`limit` must be int ≥ 1")
        limit = min(limit_raw, _MAX_RESULTS)

        root = Path(invocation.workspace_root).resolve()
        if not root.exists():
            raise ToolError(
                "exec.tool.invalid_input",
                f"workspace_root does not exist: {root}",
            )

        # We `rglob` instead of `glob` so `**/` semantics work for any
        # pattern; `Path.match` happens inside the iterator.
        results: list[str] = []
        truncated = False
        try:
            # `Path.glob` with full pattern handles `**`, `?`, `[abc]`.
            for path in root.glob(pattern):
                # Defence-in-depth: even though glob shouldn't escape
                # root, validate every hit before returning it.
                try:
                    safe = resolve_under_root(root, str(path.relative_to(root)))
                except (ValueError, WorkspaceTraversalError):
                    continue
                relative = safe.relative_to(root).as_posix()
                results.append(relative)
                if len(results) >= limit:
                    truncated = True
                    break
        except (ValueError, OSError) as exc:
            raise ToolError(
                "exec.tool.invalid_input",
                f"glob failed: {exc!s}",
            ) from exc

        results.sort()
        return ToolResult(
            content="\n".join(results),
            metadata={"count": len(results), "truncated": truncated},
        )
