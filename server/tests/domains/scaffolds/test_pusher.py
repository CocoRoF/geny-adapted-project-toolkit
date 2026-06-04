"""Phase N.2.4 — pusher integration test against a local bare repo.

We don't reach GitHub here. Instead we create a local bare repo
(`git init --bare`) + use ``file://`` as the clone URL. The pusher's
token-embed logic still runs (it's an unconditional URL rewrite), but
the resulting URL ``file://x-access-token:***@/tmp/...`` works because
git ignores the userinfo for file:// remotes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode
from gapt_server.domains.scaffolds.pusher import (
    _build_remote_url,
    _redact_token,
    push_scaffold,
)


def _init_bare(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True, capture_output=True,
    )
    return bare


@pytest.mark.asyncio
async def test_push_scaffold_writes_files_and_returns_sha(tmp_path: Path) -> None:
    bare = _init_bare(tmp_path)
    clone_url = f"file://{bare}"
    files = {
        "README.md": b"# hello\n",
        "src/main.py": b"print('hi')\n",
        ".gitignore": b"*.pyc\n",
    }

    sha = await push_scaffold(
        clone_url=clone_url,
        token="ignored_for_file_url",
        files=files,
        branch="main",
        commit_message="Initial scaffold",
    )
    # 40-char hex.
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)

    # Verify the bare repo received the commit + the file tree.
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "main"],
        check=True, capture_output=True, text=True,
    )
    pushed = set(proc.stdout.strip().split("\n"))
    assert pushed == {"README.md", "src/main.py", ".gitignore"}


@pytest.mark.asyncio
async def test_push_scaffold_refuses_empty_file_tree(tmp_path: Path) -> None:
    bare = _init_bare(tmp_path)
    with pytest.raises(ScaffoldError) as exc:
        await push_scaffold(
            clone_url=f"file://{bare}",
            token="ignored",
            files={},
            branch="main",
            commit_message="empty",
        )
    assert exc.value.code is ScaffoldErrorCode.RENDER_FAILED


def test_redact_token_strips_secret_from_url() -> None:
    url = "https://x-access-token:ghp_DEADBEEF123@github.com/octocat/my-app.git"
    assert "ghp_DEADBEEF123" not in _redact_token(url)
    assert "***" in _redact_token(url)


def test_build_remote_url_embeds_token() -> None:
    url = _build_remote_url(
        "https://github.com/octocat/my-app.git", "ghp_secret"
    )
    assert url.startswith("https://x-access-token:ghp_secret@github.com/")


def test_build_remote_url_rejects_url_without_scheme() -> None:
    with pytest.raises(ScaffoldError):
        _build_remote_url("github.com/x/y.git", "tok")
