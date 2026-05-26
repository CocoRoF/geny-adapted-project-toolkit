"""Unit tests for `workspaces.files` — no DB / no HTTP / no real shell.

The mock sandbox backend keys canned responses by the first argv
token, so each test stages results for the exact commands the file
service is expected to issue (`find` / `stat` / `cat` / `base64` /
`sh` / `rm`)."""

from __future__ import annotations

import pytest

from gapt_server.domains.sandbox import (
    ExecResult,
    SandboxCreateSpec,
)
from gapt_server.domains.workspaces.files import (
    WorkspaceFileError,
    delete_path,
    list_tree,
    read_file,
    write_file,
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


# ──────────────────────────────────────────────── traversal guard ──


@pytest.mark.asyncio
async def test_list_tree_rejects_dotdot() -> None:
    backend, ref = await _running_sandbox()
    with pytest.raises(WorkspaceFileError) as exc:
        await list_tree(backend, ref, worktree_path=WORKTREE, path="../etc")
    assert exc.value.code == "workspace.path.invalid"


@pytest.mark.asyncio
async def test_list_tree_normalises_root() -> None:
    backend, ref = await _running_sandbox()
    # `find` will be invoked with the worktree root. Stub a single
    # directory entry.
    canned = (
        f"d\t4096\t{WORKTREE}/src\0"
        f"f\t12\t{WORKTREE}/README.md\0"
    ).encode()
    backend.set_exec_response("find", _ok(stdout=canned))

    entries = await list_tree(backend, ref, worktree_path=WORKTREE, path="/")
    kinds = [(e.name, e.kind) for e in entries]
    assert kinds == [("src", "dir"), ("README.md", "file")]
    assert entries[1].size == 12
    # Workspace-relative path normalised.
    assert {e.path for e in entries} == {"/src", "/README.md"}


# ─────────────────────────────────────────────────────── read ──


@pytest.mark.asyncio
async def test_read_file_returns_utf8_when_text() -> None:
    backend, ref = await _running_sandbox()
    backend.set_exec_response("stat", _ok(stdout=b"5\n"))
    backend.set_exec_response("cat", _ok(stdout=b"hello"))

    content = await read_file(backend, ref, worktree_path=WORKTREE, path="/a.txt")
    assert content.encoding == "utf-8"
    assert content.text == "hello"


@pytest.mark.asyncio
async def test_read_file_404_when_stat_fails() -> None:
    backend, ref = await _running_sandbox()
    backend.set_exec_response(
        "stat",
        ExecResult(exit_code=1, stdout=b"", stderr=b"no such file", duration_ms=0),
    )
    with pytest.raises(WorkspaceFileError) as exc:
        await read_file(backend, ref, worktree_path=WORKTREE, path="/missing")
    assert exc.value.code == "workspace.fs.not_found"


@pytest.mark.asyncio
async def test_read_file_refuses_files_over_limit() -> None:
    backend, ref = await _running_sandbox()
    # > MAX_FILE_BYTES (1 MiB)
    backend.set_exec_response("stat", _ok(stdout=b"4000000\n"))
    with pytest.raises(WorkspaceFileError) as exc:
        await read_file(backend, ref, worktree_path=WORKTREE, path="/big.bin")
    assert exc.value.code == "workspace.fs.too_large"


# ─────────────────────────────────────────────────────── write ──


@pytest.mark.asyncio
async def test_write_file_mkdir_then_sh() -> None:
    backend, ref = await _running_sandbox()
    backend.set_exec_response("mkdir", _ok())
    backend.set_exec_response("sh", _ok())

    await write_file(
        backend, ref, worktree_path=WORKTREE, path="/src/foo.py", content="print('hi')\n"
    )
    log = backend.exec_log(ref)
    # First mkdir -p of the parent, then sh -c "echo $1 | base64 -d > $2" _ <b64> <abs>
    assert any(cmd[:3] == ["mkdir", "-p", "--"] for cmd in log)
    sh_call = next(cmd for cmd in log if cmd[0] == "sh")
    assert sh_call[1] == "-c"
    # Final arg is the absolute path inside the workspace.
    assert sh_call[-1] == f"{WORKTREE}/src/foo.py"


# ───────────────────────────────────────────────────── delete ──


@pytest.mark.asyncio
async def test_delete_refuses_root() -> None:
    backend, ref = await _running_sandbox()
    with pytest.raises(WorkspaceFileError) as exc:
        await delete_path(backend, ref, worktree_path=WORKTREE, path="/")
    assert exc.value.code == "workspace.path.invalid"


@pytest.mark.asyncio
async def test_delete_calls_rm_d() -> None:
    backend, ref = await _running_sandbox()
    backend.set_exec_response("rm", _ok())
    await delete_path(backend, ref, worktree_path=WORKTREE, path="/junk.txt")
    log = backend.exec_log(ref)
    assert log[-1] == ["rm", "-d", "--", f"{WORKTREE}/junk.txt"]
