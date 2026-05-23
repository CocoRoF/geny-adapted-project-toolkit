"""GaptPr — 3 action dispatch + protected base merge gate."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003  — pytest fixture annotation

import pytest

from gapt_runtime.tools import GaptPr, ToolError, ToolInvocation


def _inv(workspace: Path, **args: object) -> ToolInvocation:
    return ToolInvocation(name="gapt_pr", arguments=args, workspace_root=str(workspace))


class _CapturedRunner:
    """Records every gh call and replays canned responses (FIFO)."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.responses: list[tuple[int, str, str]] = []

    def queue(self, *, stdout: str = "", exit_code: int = 0, stderr: str = "") -> None:
        self.responses.append((exit_code, stdout, stderr))

    async def __call__(self, argv: list[str], cwd: str) -> tuple[int, str, str]:
        self.calls.append((argv, cwd))
        if not self.responses:
            return 0, "", ""
        return self.responses.pop(0)


def _tool() -> tuple[GaptPr, _CapturedRunner]:
    runner = _CapturedRunner()
    return GaptPr(runner=runner, gh_binary="/usr/bin/gh"), runner


# ─────────────────────────────────────────────────── dispatch ──


@pytest.mark.asyncio
async def test_unknown_action_rejected(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="approve"))
    assert exc.value.code == "exec.tool.invalid_input"


# ────────────────────────────────────────────────────── create ──


@pytest.mark.asyncio
async def test_create_happy(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(stdout="https://github.com/acme/widget/pull/42\n")
    result = await tool.execute(
        _inv(
            tmp_path,
            action="create",
            args={
                "title": "fix bug",
                "body": "long description",
                "base": "main",
                "head": "feat/x",
            },
        )
    )
    argv = runner.calls[0][0]
    assert argv[1:3] == ["pr", "create"]
    assert "--title" in argv
    assert "fix bug" in argv
    assert "--head" in argv
    assert "feat/x" in argv
    assert result.metadata == {
        "action": "create",
        "number": 42,
        "url": "https://github.com/acme/widget/pull/42",
        "draft": False,
    }


@pytest.mark.asyncio
async def test_create_draft(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(stdout="https://github.com/acme/widget/pull/9\n")
    await tool.execute(
        _inv(
            tmp_path,
            action="create",
            args={
                "title": "wip",
                "body": "",
                "base": "main",
                "head": "feat/x",
                "draft": True,
            },
        )
    )
    assert "--draft" in runner.calls[0][0]


@pytest.mark.asyncio
async def test_create_missing_title(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(
            _inv(
                tmp_path,
                action="create",
                args={"title": "   ", "base": "main", "head": "feat/x"},
            )
        )
    assert exc.value.code == "exec.tool.invalid_input"


@pytest.mark.asyncio
async def test_create_unexpected_output(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(stdout="oops not a url\n")
    with pytest.raises(ToolError) as exc:
        await tool.execute(
            _inv(
                tmp_path,
                action="create",
                args={
                    "title": "t",
                    "body": "b",
                    "base": "main",
                    "head": "feat/x",
                },
            )
        )
    assert exc.value.code == "git.pr_create_unexpected_output"


@pytest.mark.asyncio
async def test_create_gh_failure(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(exit_code=1, stderr="auth required")
    with pytest.raises(ToolError) as exc:
        await tool.execute(
            _inv(
                tmp_path,
                action="create",
                args={
                    "title": "t",
                    "body": "b",
                    "base": "main",
                    "head": "feat/x",
                },
            )
        )
    assert exc.value.code == "git.pr_create_failed"


# ──────────────────────────────────────────────── review_request ──


@pytest.mark.asyncio
async def test_review_request_adds_reviewers_and_labels(tmp_path: Path) -> None:
    tool, runner = _tool()
    result = await tool.execute(
        _inv(
            tmp_path,
            action="review_request",
            args={
                "number": 7,
                "reviewers": ["alice", "bob"],
                "labels": ["needs-review"],
            },
        )
    )
    argv = runner.calls[0][0]
    assert argv[1:3] == ["pr", "edit"]
    assert "--add-reviewer" in argv
    assert "alice,bob" in argv
    assert "--add-label" in argv
    assert "needs-review" in argv
    assert "PR #7" in result.content


@pytest.mark.asyncio
async def test_review_request_needs_at_least_one(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="review_request", args={"number": 1}))
    assert exc.value.code == "exec.tool.invalid_input"


@pytest.mark.asyncio
async def test_review_request_invalid_number(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(
            _inv(
                tmp_path,
                action="review_request",
                args={"number": 0, "reviewers": ["alice"]},
            )
        )
    assert exc.value.code == "exec.tool.invalid_input"


# ───────────────────────────────────────────────────── merge ──


@pytest.mark.asyncio
async def test_merge_happy_squash(tmp_path: Path) -> None:
    tool, runner = _tool()
    # `pr view` then `pr merge`
    runner.queue(stdout=json.dumps({"baseRefName": "develop", "headRefName": "feat/x"}))
    runner.queue(stdout="merged")
    result = await tool.execute(_inv(tmp_path, action="merge", args={"number": 11}))
    assert len(runner.calls) == 2
    assert runner.calls[0][0][1:3] == ["pr", "view"]
    assert runner.calls[1][0][1:3] == ["pr", "merge"]
    assert "--squash" in runner.calls[1][0]
    assert "--delete-branch" in runner.calls[1][0]
    assert result.metadata is not None
    assert result.metadata["strategy"] == "squash"
    assert result.metadata["base"] == "develop"


@pytest.mark.asyncio
async def test_merge_to_main_refused(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(stdout=json.dumps({"baseRefName": "main", "headRefName": "feat/x"}))
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="merge", args={"number": 1}))
    assert exc.value.code == "git.pr.merge_protected"


@pytest.mark.asyncio
async def test_merge_to_main_with_confirm(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(stdout=json.dumps({"baseRefName": "main", "headRefName": "feat/x"}))
    runner.queue(stdout="merged")
    await tool.execute(
        _inv(
            tmp_path,
            action="merge",
            args={"number": 1, "confirm_protected": True},
        )
    )
    # Did NOT raise. Two calls: view + merge.
    assert len(runner.calls) == 2


@pytest.mark.asyncio
async def test_merge_invalid_strategy(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(
            _inv(
                tmp_path,
                action="merge",
                args={"number": 1, "strategy": "octopus"},
            )
        )
    assert exc.value.code == "exec.tool.invalid_input"


@pytest.mark.asyncio
async def test_merge_view_returns_non_json(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(stdout="<html>not json</html>")
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="merge", args={"number": 1}))
    assert exc.value.code == "git.pr_view_failed"


@pytest.mark.asyncio
async def test_merge_failure_after_view(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.queue(stdout=json.dumps({"baseRefName": "develop", "headRefName": "x"}))
    runner.queue(exit_code=1, stderr="checks failing")
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="merge", args={"number": 1}))
    assert exc.value.code == "git.pr_merge_failed"
