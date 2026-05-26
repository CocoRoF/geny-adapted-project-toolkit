"""Unit tests for `workspaces.diff` — no real git, only canned mock
backend responses keyed by the first argv token (`git`)."""

from __future__ import annotations

import pytest

from gapt_server.domains.sandbox import (
    ExecResult,
    SandboxCreateSpec,
)
from gapt_server.domains.workspaces.diff import (
    MAX_DIFF_BYTES,
    WorkspaceDiffError,
    working_tree_diff,
)
from tests._helpers.fake_sandbox import FakeSandboxBackend


WORKTREE = "/workspace/demo"


async def _running_sandbox() -> tuple[FakeSandboxBackend, object]:
    backend = FakeSandboxBackend()
    ref = await backend.create(
        SandboxCreateSpec(
            project_id="p1",
            workspace_id="ws1",
            image="local/sandbox:test",
        ),
    )
    await backend.start(ref)
    return backend, ref


def _ok(stdout: bytes = b"", stderr: bytes = b"") -> ExecResult:
    return ExecResult(exit_code=0, stdout=stdout, stderr=stderr, duration_ms=0)


def _err(exit_code: int, stderr: bytes) -> ExecResult:
    return ExecResult(exit_code=exit_code, stdout=b"", stderr=stderr, duration_ms=0)


@pytest.mark.asyncio
async def test_non_git_worktree_returns_empty() -> None:
    backend, ref = await _running_sandbox()
    backend.set_exec_response("git", _err(128, b"fatal: not a git repository"))

    result = await working_tree_diff(backend, ref, worktree_path=WORKTREE)

    assert result.files == []
    assert result.unified == ""
    assert result.truncated is False


@pytest.mark.asyncio
async def test_parses_name_status_and_numstat() -> None:
    backend, ref = await _running_sandbox()
    # MockSandboxBackend.set_exec_response keys by argv[0]. All git
    # subcommands resolve to the *same* canned response — that's
    # acceptable for this happy-path test because the diff service
    # parses by stdout shape, not by which subcommand. We push a
    # multi-purpose stub: empty for the non-target calls, populated
    # for the name-status / numstat / ls-files / diff calls. Since
    # only one canned slot exists per head, we instead drive the
    # service via the *exec_log* mechanism: re-stub between calls.
    seq: list[ExecResult] = [
        _ok(stdout=b".git\n"),  # rev-parse --git-dir
        _ok(stdout=b"M\tsrc/foo.py\nA\tnew.md\n"),  # diff --name-status
        _ok(stdout=b"3\t1\tsrc/foo.py\n10\t0\tnew.md\n"),  # diff --numstat
        _ok(stdout=b"docs/draft.md\n"),  # ls-files --others
        _ok(stdout=b"diff --git a/src/foo.py b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"),
    ]

    original_exec = backend.exec_in

    async def stub(ref_inner, argv):  # type: ignore[no-untyped-def]
        # Drain seq in order; fall back to original behaviour once empty
        # so we don't surprise-pop in long tests.
        if seq:
            return seq.pop(0)
        return await original_exec(ref_inner, argv)

    backend.exec_in = stub  # type: ignore[method-assign]

    result = await working_tree_diff(backend, ref, worktree_path=WORKTREE)

    paths = [f.path for f in result.files]
    assert "src/foo.py" in paths
    assert "new.md" in paths
    assert "docs/draft.md" in paths
    foo = next(f for f in result.files if f.path == "src/foo.py")
    assert (foo.additions, foo.deletions) == (3, 1)
    untracked = next(f for f in result.files if f.path == "docs/draft.md")
    assert untracked.status == "U"
    assert "diff --git a/src/foo.py" in result.unified
    assert result.truncated is False


@pytest.mark.asyncio
async def test_unified_blob_is_capped() -> None:
    backend, ref = await _running_sandbox()

    big_diff = (b"diff --git a/x b/x\n" + b"+a line\n" * (MAX_DIFF_BYTES // 7 + 2000)).decode()
    seq: list[ExecResult] = [
        _ok(stdout=b".git\n"),
        _ok(stdout=b"M\tx\n"),
        _ok(stdout=b"1\t0\tx\n"),
        _ok(stdout=b""),  # no untracked
        _ok(stdout=big_diff.encode()),
    ]

    async def stub(ref_inner, argv):  # type: ignore[no-untyped-def]
        return seq.pop(0)

    backend.exec_in = stub  # type: ignore[method-assign]

    result = await working_tree_diff(backend, ref, worktree_path=WORKTREE)

    assert result.truncated is True
    assert "[…truncated by gapt diff cap…]" in result.unified
    assert len(result.unified.encode("utf-8")) <= MAX_DIFF_BYTES + 100  # plus the marker line


@pytest.mark.asyncio
async def test_unknown_revision_returns_empty() -> None:
    backend, ref = await _running_sandbox()
    seq: list[ExecResult] = [
        _ok(stdout=b".git\n"),  # rev-parse OK (fresh repo with no commits)
        _err(128, b"fatal: bad revision 'HEAD'"),  # name-status fails
    ]

    async def stub(ref_inner, argv):  # type: ignore[no-untyped-def]
        return seq.pop(0)

    backend.exec_in = stub  # type: ignore[method-assign]

    result = await working_tree_diff(backend, ref, worktree_path=WORKTREE)

    assert result.files == []
    assert result.unified == ""


@pytest.mark.asyncio
async def test_propagates_unexpected_failure() -> None:
    backend, ref = await _running_sandbox()
    seq: list[ExecResult] = [
        _ok(stdout=b".git\n"),
        _err(1, b"some other git error"),
    ]

    async def stub(ref_inner, argv):  # type: ignore[no-untyped-def]
        return seq.pop(0)

    backend.exec_in = stub  # type: ignore[method-assign]

    with pytest.raises(WorkspaceDiffError) as excinfo:
        await working_tree_diff(backend, ref, worktree_path=WORKTREE)
    assert excinfo.value.code == "workspace.diff.name_status_failed"
