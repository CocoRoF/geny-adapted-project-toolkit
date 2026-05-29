"""Phase C.1 follow-up — workspace containers must mount the sibling
`.bare/` directory when the worktree's `.git` is a file (worktree
layout). Without this mount the worktree's `.git` absolute `gitdir:`
reference points at a non-existent path inside the container and
every `git` command fails with `fatal: not a git repository`.

Two scenarios under test:

1. Worktree layout (Phase C.1 default) — `.git` is a file, sibling
   `.bare/` exists → argv MUST include
   `-v <parent>/.bare:<parent>/.bare:rw`.

2. Legacy clone (pre-C.1) — `.git` is a directory, no sibling
   `.bare/` → argv MUST NOT include the bare mount (would resolve
   to a non-existent dir and fail).

We stub `_run_docker` to capture the assembled argv without a real
docker daemon.
"""

from __future__ import annotations

import os

import pytest

from gapt_server.domains.workspace_sandbox import manager as ws_mod
from gapt_server.domains.workspace_sandbox.manager import WorkspaceSandbox


async def _capture_argv(
    monkeypatch: pytest.MonkeyPatch, sandbox: WorkspaceSandbox
) -> list[str]:
    calls: list[list[str]] = []

    async def fake_run_docker(*args: str, timeout_s: float = 30.0) -> tuple[int, str, str]:
        calls.append(list(args))
        if args[:2] == ("inspect", "-f"):
            return (1, "", "No such object")
        if args[:2] == ("network", "inspect"):
            return (0, "", "")
        if args[:1] == ("run",):
            return (0, "container-id\n", "")
        return (0, "", "")

    monkeypatch.setattr(ws_mod, "_run_docker", fake_run_docker)
    await sandbox.ensure()
    run_calls = [c for c in calls if c and c[0] == "run"]
    assert len(run_calls) == 1, calls
    return run_calls[0]


def _bind_mount(argv: list[str], host_path: str) -> str | None:
    """Find the `-v <host_path>:…` entry in argv and return the full
    spec, or None if absent."""
    for i, a in enumerate(argv):
        if a == "-v" and i + 1 < len(argv) and argv[i + 1].startswith(f"{host_path}:"):
            return argv[i + 1]
    return None


# ──────────────────────────────── worktree layout ──


@pytest.mark.asyncio
async def test_argv_mounts_bare_when_worktree_layout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase C.1 default: `worktree_path/.git` is a *file*
    pointing at `<parent>/.bare/worktrees/<wid>`. We must mount the
    sibling `.bare/` at the *same host path* inside the container so
    the absolute `gitdir:` reference resolves."""
    project_root = tmp_path / "demo"
    bare = project_root / ".bare"
    worktree = project_root / "01KWORK0000000000000000000"
    (bare / "worktrees" / "01KWORK0000000000000000000").mkdir(parents=True)
    worktree.mkdir()
    # `.git` is a file in worktree layout. Content doesn't matter for
    # this test — the trigger is "file vs dir".
    (worktree / ".git").write_text(
        f"gitdir: {bare}/worktrees/01KWORK0000000000000000000\n"
    )

    sandbox = WorkspaceSandbox(
        workspace_id="01KWORK0000000000000000000",
        worktree_path=str(worktree),
        image="gapt-workspace:latest",
        container_name="gapt-ws-01kwork0000000000000000000",
    )
    argv = await _capture_argv(monkeypatch, sandbox)

    spec = _bind_mount(argv, str(bare))
    assert spec == f"{bare}:{bare}:rw", argv
    # The worktree mount is still there with its destination remapped
    # to /workspace — the bare mount is purely additive.
    assert "-v" in argv
    assert f"{worktree}:/workspace" in argv


# ──────────────────────────────── legacy clone ──


@pytest.mark.asyncio
async def test_argv_skips_bare_mount_for_legacy_clone(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-C.1 workspaces have a real `.git/` *directory* and no
    `.bare` sibling. Mounting a non-existent `.bare/` would make
    docker refuse to start the container, so we must skip the
    additional mount in this case."""
    project_root = tmp_path / "legacy"
    worktree = project_root / "01KLEGACY00000000000000000"
    (worktree / ".git").mkdir(parents=True)  # `.git` is a directory

    sandbox = WorkspaceSandbox(
        workspace_id="01KLEGACY00000000000000000",
        worktree_path=str(worktree),
        image="gapt-workspace:latest",
        container_name="gapt-ws-01klegacy00000000000000000",
    )
    argv = await _capture_argv(monkeypatch, sandbox)

    bare = project_root / ".bare"
    assert _bind_mount(argv, str(bare)) is None
    # The worktree mount itself is still emitted.
    assert f"{worktree}:/workspace" in argv


# ──────────────────────────────── orphan worktree-marker ──


@pytest.mark.asyncio
async def test_argv_skips_bare_mount_when_bare_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: `.git` says it's a worktree but the sibling `.bare/`
    has been deleted (operator manually nuked it). We don't pass
    `-v <missing>:<missing>:rw` because docker would refuse to
    create the container — better to start container without git
    than to refuse boot entirely. The user gets the same
    `fatal: not a git repository` they had before; at least the
    rest of the workspace works."""
    project_root = tmp_path / "broken"
    worktree = project_root / "01KBROKEN00000000000000000"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(
        "gitdir: /nowhere/.bare/worktrees/01KBROKEN00000000000000000\n"
    )

    sandbox = WorkspaceSandbox(
        workspace_id="01KBROKEN00000000000000000",
        worktree_path=str(worktree),
        image="gapt-workspace:latest",
        container_name="gapt-ws-01kbroken00000000000000000",
    )
    argv = await _capture_argv(monkeypatch, sandbox)

    bare = project_root / ".bare"
    assert not os.path.isdir(bare)
    assert _bind_mount(argv, str(bare)) is None
