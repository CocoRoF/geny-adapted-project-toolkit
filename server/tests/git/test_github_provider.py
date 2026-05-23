"""GithubProvider — exercise every method against a captured-runner fake."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from gapt_server.domains.git import (
    GitCloneSpec,
    GithubProvider,
    GitOperationError,
    GitPushSpec,
    WorkflowRunStatus,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@pytest.fixture
def captured() -> list[list[str]]:
    return []


def _fake_runner(
    captured: list[list[str]],
    *,
    stdout: str = "",
    exit_code: int = 0,
    stderr: str = "",
) -> Callable[[list[str], dict[str, str], str | None], Awaitable[tuple[int, str, str]]]:
    async def run(argv: list[str], env: dict[str, str], cwd: str | None) -> tuple[int, str, str]:
        captured.append(argv)
        # Ensure the token is in env on every call — that's the whole
        # point of the env-pass design.
        assert env.get("GH_TOKEN") == "ghu_test"
        return exit_code, stdout, stderr

    return run


def _make(captured: list[list[str]], **kw: object) -> GithubProvider:
    return GithubProvider(
        repo="acme/widget",
        token="ghu_test",
        runner=_fake_runner(captured, **kw),  # type: ignore[arg-type]
        gh_binary="/usr/bin/gh",
    )


# ───────────────────────────────────────────────── list_user_repos ──


@pytest.mark.asyncio
async def test_list_user_repos(captured: list[list[str]]) -> None:
    body = json.dumps(
        [
            {
                "nameWithOwner": "acme/widget",
                "description": "the widget",
                "isPrivate": True,
                "defaultBranchRef": {"name": "main"},
                "url": "https://github.com/acme/widget",
            },
            {
                "nameWithOwner": "acme/gadget",
                "description": None,
                "isPrivate": False,
                "defaultBranchRef": {"name": "trunk"},
                "url": "https://github.com/acme/gadget",
            },
        ]
    )
    provider = _make(captured, stdout=body)
    repos = await provider.list_user_repos()
    assert len(repos) == 2
    assert repos[0].full_name == "acme/widget"
    assert repos[0].private is True
    assert repos[0].clone_url == "https://github.com/acme/widget.git"
    assert repos[1].default_branch == "trunk"
    assert captured[0][1:5] == ["repo", "list", "--limit", "100"]


# ──────────────────────────────────────────────────────────── clone ──


@pytest.mark.asyncio
async def test_clone_emits_expected_argv(captured: list[list[str]]) -> None:
    provider = _make(captured)
    await provider.clone(
        GitCloneSpec(
            remote_url="https://github.com/acme/widget.git",
            branch="main",
            depth=1,
            target_dir="widget",
            submodules=False,
        )
    )
    argv = captured[0]
    assert argv[1:3] == ["repo", "clone"]
    assert "--branch" in argv
    assert "--depth" in argv
    assert "1" in argv
    assert "--no-recurse-submodules" in argv


# ─────────────────────────────────────────────────────── push/fetch ──


@pytest.mark.asyncio
async def test_push_uses_force_with_lease(
    captured: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    provider = _make(captured)
    await provider.push(GitPushSpec(branch="feat/x", force_with_lease=True))
    argv = captured[0]
    assert argv[-1] == "--force-with-lease"
    assert "feat/x" in argv
    # plain --force is never emitted.
    assert "--force" not in [a for a in argv if a == "--force"]


@pytest.mark.asyncio
async def test_push_failure_maps_to_git_push_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    captured: list[list[str]] = []
    provider = GithubProvider(
        repo="acme/widget",
        token="ghu_test",
        runner=_fake_runner(captured, exit_code=128, stderr="rejected"),  # type: ignore[arg-type]
        gh_binary="/usr/bin/gh",
    )
    with pytest.raises(GitOperationError) as exc:
        await provider.push(GitPushSpec(branch="main"))
    assert exc.value.code == "git.push_failed"


# ─────────────────────────────────────────────────────────── PRs ──


@pytest.mark.asyncio
async def test_open_pr_parses_url_and_refetches() -> None:
    captured: list[list[str]] = []
    calls = {"i": 0}

    async def runner(argv: list[str], env: dict[str, str], cwd: str | None) -> tuple[int, str, str]:
        captured.append(argv)
        if calls["i"] == 0:
            calls["i"] += 1
            return 0, "https://github.com/acme/widget/pull/42\n", ""
        return (
            0,
            json.dumps(
                {
                    "number": 42,
                    "title": "fix bug",
                    "body": "details",
                    "headRefName": "feat/x",
                    "baseRefName": "main",
                    "state": "OPEN",
                    "url": "https://github.com/acme/widget/pull/42",
                    "isDraft": False,
                }
            ),
            "",
        )

    provider = GithubProvider(
        repo="acme/widget",
        token="ghu_test",
        runner=runner,
        gh_binary="/usr/bin/gh",
    )
    pr = await provider.open_pr(title="fix bug", body="details", base="main", head="feat/x")
    assert pr.number == 42
    assert pr.state == "open"
    assert captured[0][1:3] == ["pr", "create"]
    assert captured[1][1:3] == ["pr", "view"]


@pytest.mark.asyncio
async def test_open_pr_unexpected_output_raises(captured: list[list[str]]) -> None:
    provider = _make(captured, stdout="oops not a url\n")
    with pytest.raises(GitOperationError) as exc:
        await provider.open_pr(title="t", body="b", base="main", head="feat/x")
    assert exc.value.code == "git.pr_create_unexpected_output"


@pytest.mark.asyncio
async def test_get_pr_status(captured: list[list[str]]) -> None:
    body = json.dumps(
        {
            "number": 7,
            "title": "t",
            "body": "b",
            "headRefName": "feat/x",
            "baseRefName": "main",
            "state": "MERGED",
            "url": "https://github.com/acme/widget/pull/7",
            "isDraft": False,
        }
    )
    provider = _make(captured, stdout=body)
    pr = await provider.get_pr_status(number=7)
    assert pr.state == "merged"
    assert pr.number == 7


# ──────────────────────────────────────────────────── workflows ──


@pytest.mark.asyncio
async def test_list_workflow_runs_status_mapping() -> None:
    rows = [
        {
            "databaseId": 1,
            "displayTitle": "build",
            "headBranch": "main",
            "headSha": "abc",
            "status": "completed",
            "conclusion": "success",
            "url": "https://example.com/1",
        },
        {
            "databaseId": 2,
            "displayTitle": "test",
            "headBranch": "main",
            "headSha": "abc",
            "status": "completed",
            "conclusion": "failure",
            "url": "https://example.com/2",
        },
        {
            "databaseId": 3,
            "displayTitle": "deploy",
            "headBranch": "main",
            "headSha": "abc",
            "status": "in_progress",
            "conclusion": None,
            "url": "https://example.com/3",
        },
        {
            "databaseId": 4,
            "displayTitle": "neutral run",
            "headBranch": "main",
            "headSha": "abc",
            "status": "completed",
            "conclusion": "neutral",
            "url": "https://example.com/4",
        },
        {
            "databaseId": 5,
            "displayTitle": "weird",
            "headBranch": "main",
            "headSha": "abc",
            "status": "??",
            "conclusion": None,
            "url": "https://example.com/5",
        },
    ]
    captured: list[list[str]] = []
    provider = _make(captured, stdout=json.dumps(rows))
    runs = await provider.list_workflow_runs()
    statuses = [r.status for r in runs]
    assert statuses == [
        WorkflowRunStatus.COMPLETED_SUCCESS,
        WorkflowRunStatus.COMPLETED_FAILURE,
        WorkflowRunStatus.IN_PROGRESS,
        WorkflowRunStatus.COMPLETED_NEUTRAL,
        WorkflowRunStatus.UNKNOWN,
    ]


@pytest.mark.asyncio
async def test_workflow_run_logs(captured: list[list[str]]) -> None:
    provider = _make(captured, stdout="line1\nline2\n")
    text = await provider.get_workflow_run_logs(run_id=99)
    assert "line1" in text
    assert captured[0][1:3] == ["run", "view"]


# ──────────────────────────────────────────────── error mapping ──


@pytest.mark.asyncio
async def test_gh_failed_maps_to_code(captured: list[list[str]]) -> None:
    provider = _make(captured, exit_code=2, stderr="auth required")
    with pytest.raises(GitOperationError) as exc:
        await provider.list_user_repos()
    assert exc.value.code == "git.gh_failed"


@pytest.mark.asyncio
async def test_gh_malformed_json_maps_to_code(captured: list[list[str]]) -> None:
    provider = _make(captured, stdout="<html>not json</html>")
    with pytest.raises(GitOperationError) as exc:
        await provider.list_user_repos()
    assert exc.value.code == "git.gh_malformed_json"


@pytest.mark.asyncio
async def test_gh_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)

    async def runner(argv: list[str], env: dict[str, str], cwd: str | None) -> tuple[int, str, str]:
        return 0, "", ""

    provider = GithubProvider(repo="x/y", token="t", runner=runner)
    with pytest.raises(GitOperationError) as exc:
        await provider.list_user_repos()
    assert exc.value.code == "git.gh_not_found"
