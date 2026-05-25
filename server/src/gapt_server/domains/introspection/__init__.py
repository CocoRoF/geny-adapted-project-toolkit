"""Project introspection — read a workspace's worktree and infer how
to dev-serve + prod-deploy it.

The Detector walks the file tree (cheap: only roots + a couple
well-known files) and returns a `ProjectIntrospection` object the
auto-config layer turns into a `WorkspaceService` for dev + an
`Environment` for prod. Without this, the user has to type the
compose path, primary service, primary port, dev command, env file
locations, basePath flag, … six fields before anything works. With
it, "+ New project" → URL paste → done.

Detection is intentionally read-only and idempotent. It never edits
the worktree; the auto-patcher (Phase 1.5) is a separate concern
that mutates files only with explicit user consent.

Single entry point: `detect(worktree_path) -> ProjectIntrospection`.
The function chains sub-detectors in priority order and merges
their findings — see `_detector.py` for the order rationale.
"""

from gapt_server.domains.introspection._detector import detect
from gapt_server.domains.introspection._types import (
    ProjectIntrospection,
    ProjectKind,
)

__all__ = [
    "ProjectIntrospection",
    "ProjectKind",
    "detect",
]
