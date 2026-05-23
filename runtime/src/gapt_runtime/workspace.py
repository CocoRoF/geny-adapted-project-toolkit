"""Shared path-traversal guard used by every daemon-side handler.

Hoisted out of ``handlers.py`` so the new tool modules (Cycle 2.4) can
share the same canonicalisation logic — symlinks resolved, anything
outside the workspace root is refused with ``WorkspaceTraversalError``.
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceTraversalError(RuntimeError):
    """A path resolves to a location outside the workspace root."""


def resolve_under_root(root: Path, raw: str) -> Path:
    root_resolved = root.resolve(strict=False)
    candidate = (
        (root_resolved / raw).resolve(strict=False)
        if not Path(raw).is_absolute()
        else Path(raw).resolve(strict=False)
    )
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspaceTraversalError(
            f"path {raw!r} escapes workspace root {root_resolved}"
        ) from exc
    return candidate
