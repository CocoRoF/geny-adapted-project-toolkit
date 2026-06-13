"""Regression guard for the shared `_git_fetch` helper.

`_git_fetch` referenced an undefined `target` for one release, which
made Fetch / Pull / Sync 500 with a NameError on EVERY workspace —
and slipped through because the router endpoints had no test. This
exercises the helper directly (the cheapest reproduction of that
class of bug) and asserts the repo target is threaded into the git
exec so multi-repo workspaces run in the right subdir.
"""

from __future__ import annotations

import pytest

import gapt_server.routers.git as git_router
from gapt_server.routers.git import _git_fetch, _RepoTarget


@pytest.mark.asyncio
async def test_git_fetch_threads_target_into_exec(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_exec(sandbox, target, argv, *, timeout_s=30.0):
        captured["target"] = target
        captured["argv"] = argv
        return (0, "fetched", "")

    monkeypatch.setattr(git_router, "_git_exec_in", fake_exec)

    target = _RepoTarget(
        id="r1",
        subpath="frontend",
        display_name="fe",
        git_remote_url="https://x/y.git",
        git_auth_secret_ref=None,
    )
    rc, out = await _git_fetch(sandbox=object(), target=target, token=None)

    assert rc == 0
    assert out == "fetched"
    # The bug: target was undefined → NameError. Confirm it reaches exec.
    assert captured["target"] is target
    assert captured["argv"][:2] == ["fetch", "origin"]


@pytest.mark.asyncio
async def test_git_fetch_passes_auth_prefix_when_token_present(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_exec(sandbox, target, argv, *, timeout_s=30.0):
        captured["argv"] = argv
        return (0, "", "")

    monkeypatch.setattr(git_router, "_git_exec_in", fake_exec)

    target = _RepoTarget(
        id="r1",
        subpath="",
        display_name="root",
        git_remote_url="https://x/y.git",
        git_auth_secret_ref=None,
    )
    await _git_fetch(sandbox=object(), target=target, token="ghp_secret")

    argv = captured["argv"]
    # Token injected via `-c http.extraHeader=...`, never in the URL.
    assert "-c" in argv
    assert any("http.extraHeader" in str(a) for a in argv)
