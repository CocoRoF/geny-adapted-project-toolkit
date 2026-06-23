"""Snapshot git mechanics — validated against a real temp git repo on the host.

No Docker / Postgres: the capture + restore scripts the service runs inside the
workspace container are pure git, so we run the *exact same scripts* against a
throwaway repo and assert the P1 acceptance criterion — snapshot → mutate →
restore is byte-identical, including force-included build artifacts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from gapt_server.domains.snapshots.service import (
    _parse_numstat,
    build_capture_script,
    build_restore_script,
)

_ENV = {
    "GIT_AUTHOR_NAME": "GAPT",
    "GIT_AUTHOR_EMAIL": "gapt@hrletsgo.me",
    "GIT_COMMITTER_NAME": "GAPT",
    "GIT_COMMITTER_EMAIL": "gapt@hrletsgo.me",
    "GIT_CONFIG_GLOBAL": "/dev/null",  # isolate from host git config
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, env={**os.environ, **_ENV},
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _sh(repo: Path, script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", "-lc", script], cwd=repo, env={**os.environ, **_ENV, **(env or {})},
        capture_output=True, text=True,
    )


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("v1")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    return _git(repo, "rev-parse", "HEAD")


def _make_changes(repo: Path) -> None:
    (repo / "tracked.txt").write_text("v2")            # modified, uncommitted
    (repo / "new.txt").write_text("new")               # untracked
    (repo / ".gitignore").write_text("build/\n")       # ignore a build dir
    (repo / "build").mkdir(exist_ok=True)
    (repo / "build" / "artifact.bin").write_text("ARTIFACT")  # ignored artifact


def test_capture_includes_artifacts_and_restore_is_byte_identical(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    _make_changes(repo)

    # CAPTURE (tool_save semantics → include ignored artifacts).
    cap = build_capture_script(include_ignored=True, workdir=str(repo))
    res = _sh(repo, cap, {"SNAP_ID": "01TESTSNAP01", "SNAP_MSG": "snap", "SNAP_PARENT": head})
    assert res.returncode == 0, res.stderr
    commit = res.stdout.strip().splitlines()[-1]

    # The ref points at the new commit, distinct from HEAD.
    assert _git(repo, "rev-parse", "refs/snapshots/01TESTSNAP01") == commit
    assert commit != head

    # The snapshot tree contains tracked + untracked + the ignored artifact.
    files = set(_git(repo, "ls-tree", "-r", "--name-only", commit).split("\n"))
    assert {"tracked.txt", "new.txt", "build/artifact.bin"} <= files
    assert _git(repo, "show", f"{commit}:tracked.txt") == "v2"
    # The working tree / branch were NOT moved by capture.
    assert _git(repo, "rev-parse", "HEAD") == head

    # MUTATE: change tracked, delete the untracked, drop the artifact, add junk.
    (repo / "tracked.txt").write_text("v3-DIFFERENT")
    (repo / "new.txt").unlink()
    (repo / "build" / "artifact.bin").unlink()
    (repo / "junk.txt").write_text("junk")

    # RESTORE.
    rst = build_restore_script(git_sha=commit, clean=True, workdir=str(repo))
    res2 = _sh(repo, rst)
    assert res2.returncode == 0, res2.stderr

    # Byte-identical to the snapshot: artifact + untracked back, junk gone.
    assert (repo / "tracked.txt").read_text() == "v2"
    assert (repo / "new.txt").read_text() == "new"
    assert (repo / "build" / "artifact.bin").read_text() == "ARTIFACT"
    assert not (repo / "junk.txt").exists()


def test_capture_excludes_ignored_without_force(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    _make_changes(repo)

    cap = build_capture_script(include_ignored=False, workdir=str(repo))
    res = _sh(repo, cap, {"SNAP_ID": "01TESTSNAP02", "SNAP_MSG": "snap", "SNAP_PARENT": head})
    assert res.returncode == 0, res.stderr
    commit = res.stdout.strip().splitlines()[-1]

    files = set(_git(repo, "ls-tree", "-r", "--name-only", commit).split("\n"))
    assert "tracked.txt" in files and "new.txt" in files  # tracked + untracked captured
    assert "build/artifact.bin" not in files               # ignored stays out


def test_capture_on_empty_repo_no_parent(tmp_path: Path) -> None:
    # A repo with no commits yet: SNAP_PARENT empty → root snapshot commit.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "only.txt").write_text("hello")
    cap = build_capture_script(include_ignored=True, workdir=str(repo))
    res = _sh(repo, cap, {"SNAP_ID": "01TESTSNAP03", "SNAP_MSG": "root", "SNAP_PARENT": ""})
    assert res.returncode == 0, res.stderr
    commit = res.stdout.strip().splitlines()[-1]
    files = set(_git(repo, "ls-tree", "-r", "--name-only", commit).split("\n"))
    assert "only.txt" in files


def test_parse_numstat() -> None:
    out = _parse_numstat("3\t1\ta.py\n-\t-\tbin.dat\n10\t0\tb.py\n")
    assert out == {"files": 3, "additions": 13, "deletions": 1}
