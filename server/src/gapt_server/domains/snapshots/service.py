"""Workspace snapshot capture / restore / diff — the git-grade, AI-first
checkpoint engine.

A snapshot is a commit on ``refs/snapshots/<id>`` that captures the workspace's
full working-tree state (for ``tool_save`` snapshots, build artifacts are
force-included so a cold restore reproduces a working environment), plus the
agent activity that produced it (the ``session_events`` seq range + a compact
transcript), chained into a DAG by ``parent_id``.

All git runs inside the workspace container via ``WorkspaceSandbox.exec`` so it
sees the same filesystem + git config the agent used. Capture never touches the
working tree, the real index, or the current branch: it stages into a throwaway
``GIT_INDEX_FILE`` and writes the commit with ``git commit-tree`` straight to a
reserved ref.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from gapt_server.agent.transcript import build_transcript
from gapt_server.db import enums, models

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.domains.workspace_sandbox import WorkspaceSandbox

# git identity for snapshot commits (mirrors routers/git.py).
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "GAPT",
    "GIT_AUTHOR_EMAIL": "gapt@hrletsgo.me",
    "GIT_COMMITTER_NAME": "GAPT",
    "GIT_COMMITTER_EMAIL": "gapt@hrletsgo.me",
    "GIT_TERMINAL_PROMPT": "0",
}

_MAX_TURN_TEXT = 4000      # cap assistant/user text per turn in the stored activity
_MAX_TOOL_FIELD = 1000     # cap tool input/output blobs in the stored activity
_CAPTURE_TIMEOUT_S = 180.0  # artifact-heavy `git add -f -A` can be slow
_GIT_TIMEOUT_S = 60.0


class SnapshotError(RuntimeError):
    """Carries a stable ``code`` the router maps to an HTTP error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ── git scripts (extracted so they're unit-testable against host git) ───
# Capture stages the whole working tree into a THROWAWAY index and writes a
# commit straight to refs/snapshots/$SNAP_ID via commit-tree — the real index,
# working tree, and current branch are never touched. Env in: SNAP_ID, SNAP_MSG,
# SNAP_PARENT (commit sha or ""). Echoes the new commit sha (or __NOGIT__).


def build_capture_script(*, include_ignored: bool, workdir: str = "/workspace") -> str:
    force = "-f " if include_ignored else ""
    return (
        "set -e\n"
        f"cd {workdir}\n"
        # git is the snapshot substrate. A workspace with no repo (empty
        # project / no clone) is still snapshottable — initialise one so the
        # working tree can be captured. Idempotent; identity comes from the
        # GIT_AUTHOR_*/COMMITTER_* env the caller sets.
        "if ! git rev-parse --git-dir >/dev/null 2>&1; then git init -q; fi\n"
        'TMPIDX="$(mktemp -u)"\n'
        'export GIT_INDEX_FILE="$TMPIDX"\n'
        f"git add -A {force}.\n"
        'TREE="$(git write-tree)"\n'
        'if [ -n "$SNAP_PARENT" ]; then\n'
        '  COMMIT="$(git commit-tree "$TREE" -p "$SNAP_PARENT" -m "$SNAP_MSG")"\n'
        "else\n"
        '  COMMIT="$(git commit-tree "$TREE" -m "$SNAP_MSG")"\n'
        "fi\n"
        'git update-ref "refs/snapshots/$SNAP_ID" "$COMMIT"\n'
        'rm -f "$TMPIDX" 2>/dev/null || true\n'
        'echo "$COMMIT"\n'
    )


def build_restore_script(*, git_sha: str, clean: bool, workdir: str = "/workspace") -> str:
    clean_cmd = "git clean -fd\n" if clean else ""
    return (
        "set -e\n"
        f"cd {workdir}\n"
        f'git reset --hard "{git_sha}"\n'
        f"{clean_cmd}"
        "git rev-parse HEAD\n"
    )


# ── low-level shell in the workspace container ──────────────────────────


async def _sh(
    sandbox: "WorkspaceSandbox",
    script: str,
    *,
    env: dict[str, str] | None = None,
    timeout_s: float = _GIT_TIMEOUT_S,
) -> tuple[int, str, str]:
    rc, out, err = await sandbox.exec(
        ["sh", "-lc", script],
        env={**_GIT_ENV, **(env or {})},
        cwd="/workspace",
        timeout_s=timeout_s,
    )
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# ── helpers ─────────────────────────────────────────────────────────────


def _trunc(text: Any, limit: int) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return s
    return s[:limit] + f"…(+{len(s) - limit} chars)"


def _compact_activity(transcript: Any) -> dict[str, Any]:
    """Shrink a Transcript into a bounded JSON blob for durable storage."""
    turns = []
    for t in transcript.turns:
        turns.append(
            {
                "user": _trunc(t.user, _MAX_TURN_TEXT),
                "assistant": _trunc(t.assistant, _MAX_TURN_TEXT),
                "cost_usd": round(float(t.cost_usd or 0.0), 6),
                "tool_uses": [
                    {
                        "tool": tu.tool,
                        "input": _trunc(tu.input, _MAX_TOOL_FIELD) if tu.input is not None else None,
                        "output": _trunc(tu.output, _MAX_TOOL_FIELD) if tu.output is not None else None,
                        "is_error": bool(tu.is_error),
                    }
                    for tu in t.tool_uses
                ],
            }
        )
    return {"turns": turns, "total_cost_usd": round(float(transcript.total_cost_usd or 0.0), 6)}


async def _build_activity(
    db: "AsyncSession", *, session_id: str, start_seq: int, end_seq: int
) -> tuple[dict[str, Any], int, int]:
    """Compact transcript of session_events in ``(start_seq, end_seq]``."""
    rows = (
        await db.execute(
            select(models.SessionEvent)
            .where(
                models.SessionEvent.session_id == session_id,
                models.SessionEvent.seq > start_seq,
                models.SessionEvent.seq <= end_seq,
            )
            .order_by(models.SessionEvent.seq)
        )
    ).scalars().all()
    events = [
        {"kind": r.kind, "data": r.data, "ts": r.ts.isoformat() if r.ts else None, "seq": r.seq}
        for r in rows
    ]
    transcript = build_transcript(session_id=session_id, events=events)
    return _compact_activity(transcript), start_seq, end_seq


def _parse_numstat(text: str) -> dict[str, int]:
    files = adds = dels = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        files += 1
        a, d, _ = parts
        if a != "-":
            try:
                adds += int(a)
            except ValueError:
                pass
        if d != "-":
            try:
                dels += int(d)
            except ValueError:
                pass
    return {"files": files, "additions": adds, "deletions": dels}


# ── public API ──────────────────────────────────────────────────────────


async def list_for_workspace(
    db: "AsyncSession", *, workspace_id: str
) -> list[models.Snapshot]:
    return list(
        (
            await db.execute(
                select(models.Snapshot)
                .where(models.Snapshot.workspace_id == workspace_id)
                .order_by(models.Snapshot.created_at.desc())
            )
        ).scalars().all()
    )


async def get(db: "AsyncSession", *, snapshot_id: str) -> models.Snapshot | None:
    return await db.get(models.Snapshot, snapshot_id)


async def _latest_for_workspace(
    db: "AsyncSession", *, workspace_id: str
) -> models.Snapshot | None:
    return (
        await db.execute(
            select(models.Snapshot)
            .where(models.Snapshot.workspace_id == workspace_id)
            .order_by(models.Snapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def capture(
    db: "AsyncSession",
    *,
    sandbox: "WorkspaceSandbox",
    workspace: models.Workspace,
    session_id: str | None,
    kind: enums.SnapshotKind,
    label: str = "",
    include_ignored: bool | None = None,
    created_by: str | None = None,
) -> models.Snapshot:
    """Capture a snapshot of ``workspace``'s current state + agent activity.

    ``include_ignored`` defaults to True for ``tool_save`` (capture build
    artifacts for exact reproduction) and False otherwise.
    """
    if include_ignored is None:
        include_ignored = kind == enums.SnapshotKind.TOOL_SAVE

    # 1. Resolve the parent commit: prior snapshot of this workspace forms the
    #    DAG edge; the very first snapshot anchors to HEAD (if the repo has one)
    #    so its diff is meaningful, but parent_id stays NULL.
    prior = await _latest_for_workspace(db, workspace_id=workspace.id)
    parent_id = prior.id if prior else None
    if prior is not None:
        parent_sha = prior.git_sha
    else:
        rc, head_out, _ = await _sh(sandbox, "git rev-parse HEAD 2>/dev/null || true")
        parent_sha = head_out.strip() if rc == 0 else ""

    # 2. Snapshot id (so the ref name is known before the row is flushed).
    from gapt_server.db.ulid import ulid_default  # noqa: PLC0415

    snap_id = ulid_default()
    script = build_capture_script(include_ignored=include_ignored)
    rc, out, err = await _sh(
        sandbox,
        script,
        env={
            "SNAP_ID": snap_id,
            "SNAP_MSG": label or f"snapshot {snap_id}",
            "SNAP_PARENT": parent_sha,
        },
        timeout_s=_CAPTURE_TIMEOUT_S,
    )
    out = out.strip()
    if rc != 0:
        raise SnapshotError("snapshot.capture_failed", (err.strip() or out)[:400])
    if out == "__NOGIT__" or not out:
        raise SnapshotError(
            "snapshot.not_a_git_workspace",
            "workspace has no git repo to snapshot",
        )
    git_sha = out.splitlines()[-1].strip()
    git_ref = f"refs/snapshots/{snap_id}"

    # 3. Stats — diff of this commit vs its git parent (force `--format=` off so
    #    only the numstat rows come back).
    _, numstat_out, _ = await _sh(
        sandbox, f"git show --numstat --format= {git_sha} 2>/dev/null || true"
    )
    stats: dict[str, Any] = _parse_numstat(numstat_out)

    # 4. Activity — the chat + tool trail in the new event range.
    activity: dict[str, Any] = {}
    start_seq: int | None = None
    end_seq: int | None = None
    if session_id:
        max_seq = (
            await db.execute(
                select(func.max(models.SessionEvent.seq)).where(
                    models.SessionEvent.session_id == session_id
                )
            )
        ).scalar_one_or_none()
        if max_seq is not None:
            start_seq = prior.event_end_seq if (prior and prior.event_end_seq is not None) else 0
            end_seq = int(max_seq)
            activity, start_seq, end_seq = await _build_activity(
                db, session_id=session_id, start_seq=start_seq, end_seq=end_seq
            )
            stats["turns"] = len(activity.get("turns", []))
            stats["tool_calls"] = sum(len(t.get("tool_uses", [])) for t in activity.get("turns", []))

    # 5. Persist.
    snap = models.Snapshot(
        id=snap_id,
        workspace_id=workspace.id,
        session_id=session_id,
        parent_id=parent_id,
        kind=kind,
        label=label,
        git_ref=git_ref,
        git_sha=git_sha,
        event_start_seq=start_seq,
        event_end_seq=end_seq,
        stats=stats,
        activity=activity,
        created_by=created_by,
    )
    db.add(snap)
    await db.flush()
    return snap


async def restore(
    db: "AsyncSession",
    *,
    sandbox: "WorkspaceSandbox",
    snapshot: models.Snapshot,
    clean: bool = True,
) -> dict[str, Any]:
    """Reset the workspace working tree to the snapshot's commit.

    Artifacts that were force-included are tracked in the snapshot commit, so a
    hard reset brings them back; ``clean`` removes files created after the
    snapshot so the tree matches the checkpoint.
    """
    script = build_restore_script(git_sha=snapshot.git_sha, clean=clean)
    rc, out, err = await _sh(sandbox, script, timeout_s=_CAPTURE_TIMEOUT_S)
    if rc != 0:
        raise SnapshotError("snapshot.restore_failed", (err.strip() or out.strip())[:400])
    return {"restored_to": snapshot.git_sha, "head": out.strip().splitlines()[-1] if out.strip() else ""}


async def compute_diff(
    *, sandbox: "WorkspaceSandbox", snapshot: models.Snapshot, max_bytes: int = 256 * 1024
) -> dict[str, Any]:
    """The unified diff this snapshot introduced (vs its git parent), computed
    on demand from the commits (git is the durable record)."""
    rc, out, _ = await _sh(
        sandbox,
        f"git show --no-color --format= {snapshot.git_sha} 2>/dev/null || true",
        timeout_s=_GIT_TIMEOUT_S,
    )
    truncated = False
    if len(out.encode("utf-8")) > max_bytes:
        sliced = out.encode("utf-8")[:max_bytes]
        nl = sliced.rfind(b"\n")
        if nl > 0:
            sliced = sliced[:nl]
        out = sliced.decode("utf-8", "replace") + "\n[…truncated…]\n"
        truncated = True
    return {"unified": out, "truncated": truncated, "stats": snapshot.stats}


async def delete(
    db: "AsyncSession", *, sandbox: "WorkspaceSandbox | None", snapshot: models.Snapshot
) -> None:
    """Remove the snapshot row + its git ref (the commit object is left to git
    GC). Best-effort on the ref — the row removal is the source of truth."""
    if sandbox is not None:
        try:
            await _sh(
                sandbox,
                f'git update-ref -d "{snapshot.git_ref}" 2>/dev/null || true',
                timeout_s=_GIT_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — ref cleanup never blocks the delete
            pass
    await db.delete(snapshot)
    await db.flush()
