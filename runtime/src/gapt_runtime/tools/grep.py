"""``gapt_grep`` — regex search over workspace text files.

Pure-Python; no ``rg`` system dep. Walks the workspace (optionally
restricted to a subpath) and returns matches in
``path:line:column:text`` form, capped at ``max_matches``.

Binary files (anything where the first 8 KiB contains a NUL byte) are
skipped — matches LLM expectations and avoids dumping JPEGs to chat.
"""

from __future__ import annotations

import re
from pathlib import Path

from gapt_runtime.tools.protocol import (
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchema,
)
from gapt_runtime.workspace import WorkspaceTraversalError, resolve_under_root

_DEFAULT_MAX_MATCHES = 1_000
_BINARY_PROBE_BYTES = 8 * 1_024


class GaptGrep:
    name = "gapt_grep"
    description = (
        "Search files in the workspace for a regex pattern. Returns "
        "`path:line:col:matched_line` for every hit, capped at "
        "`max_matches` (default 1000). Skips binary files."
    )
    schema = ToolSchema(
        properties={
            "pattern": {"type": "string", "description": "Python `re` pattern."},
            "path": {
                "type": "string",
                "description": "Subpath to search (default: whole workspace).",
            },
            "max_matches": {
                "type": "integer",
                "minimum": 1,
                "description": "Stop after this many hits.",
            },
            "ignore_case": {"type": "boolean", "description": "Case-insensitive match."},
        },
        required=("pattern",),
    )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        args = invocation.arguments
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("exec.tool.invalid_input", "`pattern` must be a non-empty string")
        max_matches = args.get("max_matches", _DEFAULT_MAX_MATCHES)
        if not isinstance(max_matches, int) or max_matches < 1:
            raise ToolError("exec.tool.invalid_input", "`max_matches` must be int ≥ 1")
        ignore_case = bool(args.get("ignore_case", False))
        subpath = args.get("path")

        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError("exec.tool.invalid_input", f"invalid regex: {exc!s}") from exc

        root = Path(invocation.workspace_root).resolve()
        if subpath:
            try:
                search_root = resolve_under_root(root, subpath)
            except WorkspaceTraversalError as exc:
                raise ToolError("exec.tool.access_denied", str(exc)) from exc
        else:
            search_root = root

        hits: list[str] = []
        truncated = False
        files_scanned = 0
        files_skipped_binary = 0

        targets: list[Path]
        if search_root.is_file():
            targets = [search_root]
        else:
            targets = sorted(p for p in search_root.rglob("*") if p.is_file())

        for path in targets:
            try:
                head = path.read_bytes()[:_BINARY_PROBE_BYTES]
            except OSError:
                continue
            if b"\x00" in head:
                files_skipped_binary += 1
                continue
            files_scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            for line_no, line in enumerate(text.splitlines(), start=1):
                for match in compiled.finditer(line):
                    hits.append(f"{rel}:{line_no}:{match.start() + 1}:{line}")
                    if len(hits) >= max_matches:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break

        return ToolResult(
            content="\n".join(hits),
            metadata={
                "match_count": len(hits),
                "files_scanned": files_scanned,
                "files_skipped_binary": files_skipped_binary,
                "truncated": truncated,
            },
        )
