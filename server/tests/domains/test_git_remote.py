"""Unit tests for the ls-remote helper.

We avoid hitting a real remote — every test monkeypatches
`asyncio.create_subprocess_exec` so the parser + cache + error path
are exercised deterministically.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gapt_server.domains import git_remote


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test starts with an empty cache so ordering doesn't leak."""
    git_remote._cache.clear()


class _FakeProc:
    """Mimics the subset of `asyncio.subprocess.Process` ls-remote
    code touches: `communicate()` + `returncode` + `kill`/`wait`."""

    def __init__(self, *, returncode: int, stdout: bytes, stderr: bytes, delay_s: float = 0) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._delay_s = delay_s
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc) -> dict[str, Any]:
    """Capture the argv the helper would have invoked + return `proc`
    when `create_subprocess_exec` is awaited."""
    captured: dict[str, Any] = {}

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["argv"] = args
        captured["env"] = kwargs.get("env")
        return proc

    monkeypatch.setattr(git_remote.asyncio, "create_subprocess_exec", _fake_exec)
    return captured


# ─────────────────────────────────────────── parser ──


def test_parse_extracts_head_and_branches() -> None:
    out = (
        "ref: refs/heads/main\tHEAD\n"
        "abc123\tHEAD\n"
        "abc123\trefs/heads/main\n"
        "def456\trefs/heads/develop\n"
        "ghi789\trefs/heads/feat/auth-rework\n"
    )
    head, branches = git_remote._parse(out)
    assert head == "main"
    assert branches == ["main", "develop", "feat/auth-rework"]


def test_parse_handles_missing_symref() -> None:
    """Some remotes (gitolite / weird forges) don't emit a symref
    line. We should still return the heads list with head=None."""
    out = "abc\trefs/heads/x\ndef\trefs/heads/y\n"
    head, branches = git_remote._parse(out)
    assert head is None
    assert branches == ["x", "y"]


def test_parse_ignores_tags_and_garbage() -> None:
    out = (
        "ref: refs/heads/master\tHEAD\n"
        "abc\trefs/heads/master\n"
        "def\trefs/tags/v1.0\n"
        "malformed-line-without-tab\n"
    )
    head, branches = git_remote._parse(out)
    assert head == "master"
    assert branches == ["master"]


# ──────────────────────────────────────── success path ──


@pytest.mark.asyncio
async def test_success_returns_branches_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(
        returncode=0,
        stdout=(
            b"ref: refs/heads/main\tHEAD\n"
            b"abc\tHEAD\n"
            b"abc\trefs/heads/main\n"
            b"def\trefs/heads/feat/a\n"
        ),
        stderr=b"",
    )
    captured = _patch_subprocess(monkeypatch, proc)

    result = await git_remote.list_remote_branches(
        project_id="p1",
        git_remote_url="https://github.com/example/repo.git",
        github_token=None,
    )
    assert result.head == "main"
    assert result.branches == ["main", "feat/a"]

    # Subsequent call must hit the cache (no new subprocess attempt).
    sentinel = object()
    monkeypatch.setattr(
        git_remote.asyncio,
        "create_subprocess_exec",
        lambda *a, **kw: sentinel,  # would raise TypeError if awaited
    )
    cached = await git_remote.list_remote_branches(
        project_id="p1",
        git_remote_url="https://github.com/example/repo.git",
        github_token=None,
    )
    assert cached is result  # identity — cache returns the same object

    # And the argv contained the URL + ls-remote, no token.
    argv = captured["argv"]
    assert "ls-remote" in argv
    assert "--symref" in argv
    assert "https://github.com/example/repo.git" in argv
    assert not any("http.extraHeader" in str(a) for a in argv)


@pytest.mark.asyncio
async def test_token_injected_as_extra_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-None github_token must land in argv as
    `-c http.extraHeader=Authorization: Basic …`, never on stdin /
    in the URL."""
    proc = _FakeProc(returncode=0, stdout=b"", stderr=b"")
    captured = _patch_subprocess(monkeypatch, proc)

    await git_remote.list_remote_branches(
        project_id="p-tok",
        git_remote_url="https://github.com/example/private.git",
        github_token="ghp_test123",
    )
    argv = " ".join(str(a) for a in captured["argv"])
    assert "-c" in captured["argv"]
    assert "http.extraHeader=Authorization: Basic " in argv
    # Raw token must NOT appear — only base64-encoded in the header.
    assert "ghp_test123" not in argv


@pytest.mark.asyncio
async def test_invalidate_busts_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    proc1 = _FakeProc(returncode=0, stdout=b"abc\trefs/heads/old\n", stderr=b"")
    _patch_subprocess(monkeypatch, proc1)
    first = await git_remote.list_remote_branches(
        project_id="p2",
        git_remote_url="x",
        github_token=None,
    )
    assert first.branches == ["old"]

    git_remote.invalidate("p2")

    proc2 = _FakeProc(returncode=0, stdout=b"def\trefs/heads/new\n", stderr=b"")
    _patch_subprocess(monkeypatch, proc2)
    second = await git_remote.list_remote_branches(
        project_id="p2",
        git_remote_url="x",
        github_token=None,
    )
    assert second.branches == ["new"]


# ─────────────────────────────────────────── errors ──


@pytest.mark.asyncio
async def test_nonzero_exit_raises_with_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's last stderr line is what the UI shows. Make sure we
    surface that and not just a generic 'ls-remote failed'."""
    proc = _FakeProc(
        returncode=128,
        stdout=b"",
        stderr=(
            b"warning: chatter\n"
            b"fatal: Authentication failed for "
            b"'https://github.com/example/private.git/'\n"
        ),
    )
    _patch_subprocess(monkeypatch, proc)

    with pytest.raises(git_remote.RemoteBranchesError) as exc_info:
        await git_remote.list_remote_branches(
            project_id="p3",
            git_remote_url="https://github.com/example/private.git",
            github_token=None,
        )
    assert "Authentication failed" in exc_info.value.reason
    # Failed lookups must NOT be cached — re-asking after a token fix
    # should retry.
    assert "p3" not in git_remote._cache


@pytest.mark.asyncio
async def test_timeout_kills_proc_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(returncode=0, stdout=b"", stderr=b"", delay_s=10.0)
    _patch_subprocess(monkeypatch, proc)
    # Squash the real timeout so the test doesn't actually wait 20s.
    monkeypatch.setattr(git_remote, "_LS_REMOTE_TIMEOUT_S", 0.05)

    with pytest.raises(git_remote.RemoteBranchesError) as exc_info:
        await git_remote.list_remote_branches(
            project_id="p4",
            git_remote_url="https://unreachable.example/repo.git",
            github_token=None,
        )
    assert "timed out" in exc_info.value.reason
    assert proc.killed is True
