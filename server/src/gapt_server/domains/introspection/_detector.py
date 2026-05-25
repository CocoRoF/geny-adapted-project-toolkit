"""Detection entry point + merger.

Sub-detectors run in priority order and each contributes fields to
the rolling `ProjectIntrospection`. We prefer compose findings as
the ground truth for multi-service projects (the user committed
their topology; we trust it), then fall back to package.json /
pyproject for what the dev surface looks like.

Why merge instead of pick-one: a single repo often has both
compose-based prod AND a `package.json` describing dev. Both
detectors fire and contribute different fields — compose fills
`prod_*`, package.json fills `dev_*` + `kind`. Merging avoids the
"detector hierarchy beats the actual truth" problem.
"""

from __future__ import annotations

from pathlib import Path

from gapt_server.domains.introspection._compose import detect_compose
from gapt_server.domains.introspection._node import detect_node
from gapt_server.domains.introspection._python import detect_python
from gapt_server.domains.introspection._types import (
    ProjectIntrospection,
    ProjectKind,
)


def detect(worktree_path: str | Path) -> ProjectIntrospection:
    """Run every sub-detector against the worktree, merge results.

    Order matters for the *kind* field — later detectors only
    overwrite kind when the prior kind was UNKNOWN. Other fields
    merge by "first non-empty wins" per field, so a compose detector
    setting `prod_*` doesn't get clobbered by a Node detector that
    has nothing to say about prod.
    """
    root = Path(worktree_path)
    if not root.is_dir():
        return ProjectIntrospection(
            notes=[f"worktree {worktree_path!r} not found"], confidence=0.0
        )

    # Order: compose is the most expressive ground truth, run first.
    # Node + Python fill in dev-mode details and the "kind" hint.
    detectors = [detect_compose, detect_node, detect_python]
    merged = ProjectIntrospection()
    for fn in detectors:
        try:
            partial = fn(root)
        except Exception as exc:  # noqa: BLE001 — each detector is best-effort
            merged = merged.with_update(
                notes=[*merged.notes, f"{fn.__name__} failed: {exc}"]
            )
            continue
        merged = _merge(merged, partial)
    # Roll up confidence: any single detector with high confidence
    # gives the result high confidence (an explicit compose file is
    # near-certain).
    return merged


def _merge(
    a: ProjectIntrospection, b: ProjectIntrospection
) -> ProjectIntrospection:
    """Field-by-field merge. `a` is the accumulator (older
    findings), `b` is the fresh contribution. Strategy:

    * scalars: keep `a`'s value if set, else take `b`'s.
    * `kind`: keep `a` unless it's UNKNOWN, then take `b`.
    * lists: extend with deduplication.
    * dicts: shallow merge, `b` wins on key collision (later
      detectors get to refine env hints).
    * confidence: max — we trust the most-confident detector.
    """
    kind = a.kind if a.kind != ProjectKind.UNKNOWN else b.kind

    def first(av: object, bv: object) -> object:
        return av if av not in (None, "", 0, False) else bv

    def merge_list(av: list[str], bv: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in (*av, *bv):
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return ProjectIntrospection(
        kind=kind,
        has_compose=a.has_compose or b.has_compose,
        secondary_stacks=merge_list(a.secondary_stacks, b.secondary_stacks),
        dev_command=first(a.dev_command, b.dev_command),  # type: ignore[arg-type]
        dev_port=first(a.dev_port, b.dev_port),  # type: ignore[arg-type]
        dev_cwd=first(a.dev_cwd, b.dev_cwd),  # type: ignore[arg-type]
        dev_env_hints={**a.dev_env_hints, **b.dev_env_hints},
        prod_compose_path=first(a.prod_compose_path, b.prod_compose_path),  # type: ignore[arg-type]
        prod_compose_paths=merge_list(a.prod_compose_paths, b.prod_compose_paths),
        prod_primary_service=first(a.prod_primary_service, b.prod_primary_service),  # type: ignore[arg-type]
        prod_primary_port=first(a.prod_primary_port, b.prod_primary_port),  # type: ignore[arg-type]
        prod_build_required=a.prod_build_required or b.prod_build_required,
        env_files=merge_list(a.env_files, b.env_files),
        env_examples=merge_list(a.env_examples, b.env_examples),
        needs_basepath=a.needs_basepath or b.needs_basepath,
        basepath_config_file=first(a.basepath_config_file, b.basepath_config_file),  # type: ignore[arg-type]
        confidence=max(a.confidence, b.confidence),
        notes=merge_list(a.notes, b.notes),
        sources=merge_list(a.sources, b.sources),
    )
