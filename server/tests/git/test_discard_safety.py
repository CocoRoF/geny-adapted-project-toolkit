"""Path-safety contract for `git_discard`.

`git_discard` (routers/git.py) runs `git restore --worktree` / `git
clean -f` against caller-supplied paths inside `/workspace`. If it
accepted absolute paths, `..` traversal, or `.gapt/`-managed scratch
paths it would become a generic fs-delete primitive reaching outside
the worktree (or nuking GAPT's own runtime state). The full endpoint
needs a DB + sandbox, so we exercise the two pure predicates the
endpoint delegates to instead — that's the cheapest reproduction of a
path-safety regression.
"""

from __future__ import annotations

import inspect

import pytest

import gapt_server.routers.git as git_router
from gapt_server.routers.git import (
    _is_gapt_managed_path,
    _is_safe_discard_path,
)


@pytest.mark.parametrize(
    "path",
    [
        ".gapt/services/x.log",
        ".gapt",
    ],
)
def test_gapt_managed_paths_detected(path: str) -> None:
    assert _is_gapt_managed_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/main.py",
        "README.md",
        # `.gapt`-prefixed but NOT the managed dir — must not over-match.
        ".gapttrap/x",
        ".gaptrc",
    ],
)
def test_ordinary_paths_not_gapt_managed(path: str) -> None:
    assert _is_gapt_managed_path(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "src/app.ts",
        "src/main.py",
        "README.md",
        "a/b/c.txt",
        ".gitignore",
    ],
)
def test_safe_discard_accepts_ordinary_relative_paths(path: str) -> None:
    assert _is_safe_discard_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # absolute paths escape the worktree
        "/etc/passwd",
        "/workspace/src/x",
        # parent-dir traversal
        "../x",
        "a/../b",
        "a/b/../../../etc",
        # empty / whitespace-only-ish
        "",
        # `.gapt/`-managed scratch
        ".gapt/foo",
        ".gapt",
        ".gapt/services/x.log",
    ],
)
def test_safe_discard_rejects_unsafe_paths(path: str) -> None:
    assert _is_safe_discard_path(path) is False


def test_git_discard_delegates_to_safe_predicate() -> None:
    """Guard the refactor: the endpoint must route every path through
    `_is_safe_discard_path` rather than re-inlining a filter that could
    drift from this test's contract."""
    src = inspect.getsource(git_router.git_discard)
    assert "_is_safe_discard_path" in src
