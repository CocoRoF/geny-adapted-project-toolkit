"""``gapt_edit`` — single-file find-and-replace.

Refuses unless the `old` text appears *exactly once* (or the caller
opts into ``all=true``). That mirrors how a careful human edit works
and prevents silent multi-site changes the LLM didn't intend.

`gapt_edit` is the only mutating tool in the M1-E2 four-pack. Every
call still goes through the daemon's JWT-protected `/tools/call`
endpoint plus (in Cycle 2.9) the PolicyEngine hook.
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


class GaptEdit:
    name = "gapt_edit"
    description = (
        "Replace `old` with `new` inside a workspace file. "
        "Refuses unless `old` is found exactly once, or `all=true`. "
        "Use `gapt_read` first to confirm context."
    )
    schema = ToolSchema(
        properties={
            "path": {"type": "string", "description": "Workspace-relative file path."},
            "old": {"type": "string", "description": "Exact text to find."},
            "new": {"type": "string", "description": "Replacement text."},
            "all": {
                "type": "boolean",
                "description": "Replace every occurrence (default false).",
            },
        },
        required=("path", "old", "new"),
    )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments
        raw_path = args.get("path")
        old = args.get("old")
        new = args.get("new")
        replace_all = bool(args.get("all", False))

        if not isinstance(raw_path, str) or not raw_path:
            raise ToolError("exec.tool.invalid_input", "`path` must be a non-empty string")
        if not isinstance(old, str) or not old:
            raise ToolError("exec.tool.invalid_input", "`old` must be a non-empty string")
        if not isinstance(new, str):
            raise ToolError("exec.tool.invalid_input", "`new` must be a string")
        if old == new:
            raise ToolError("exec.tool.invalid_input", "`old` == `new` — nothing to do")

        try:
            target = resolve_under_root(Path(invocation.workspace_root), raw_path)
        except WorkspaceTraversalError as exc:
            raise ToolError("exec.tool.access_denied", str(exc)) from exc
        if not target.exists():
            raise ToolError("exec.tool.invalid_input", f"path not found: {raw_path}")
        if not target.is_file():
            raise ToolError("exec.tool.invalid_input", f"not a regular file: {raw_path}")

        original = target.read_text(encoding="utf-8", errors="strict")
        occurrences = original.count(old)
        if occurrences == 0:
            raise ToolError(
                "exec.tool.invalid_input",
                f"`old` text not found in {raw_path}",
            )
        if occurrences > 1 and not replace_all:
            raise ToolError(
                "exec.tool.invalid_input",
                f"`old` text found {occurrences} times in {raw_path}; "
                "pass `all=true` to replace every occurrence",
            )

        if replace_all:
            mutated = original.replace(old, new)
            replaced = occurrences
        else:
            mutated = original.replace(old, new, 1)
            replaced = 1

        target.write_text(mutated, encoding="utf-8")
        return ToolResult(
            content=f"replaced {replaced} occurrence(s) in {raw_path}",
            # `path / old / new` are echoed so the UI can render a
            # diff card from the SSE tool_result event without a
            # follow-up read (Cycle 3.6). They're already in `args`
            # but the chat layer never sees `args` on its own — only
            # the result.
            metadata={
                "replaced": replaced,
                "all": replace_all,
                "path": raw_path,
                "old": old,
                "new": new,
            },
        )
