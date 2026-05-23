"""GaptGit — 8 action dispatch + protected-branch gate + auto coauthor."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  — pytest fixture annotation

import pytest

from gapt_runtime.tools import GaptGit, ToolError, ToolInvocation


def _inv(workspace: Path, **args: object) -> ToolInvocation:
    return ToolInvocation(name="gapt_git", arguments=args, workspace_root=str(workspace))


class _CapturedRunner:
    """Records every git call and replays canned responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.exit_code: int = 0
        self.stdout: str = ""
        self.stderr: str = ""

    async def __call__(self, argv: list[str], cwd: str) -> tuple[int, str, str]:
        self.calls.append((argv, cwd))
        return self.exit_code, self.stdout, self.stderr


def _tool() -> tuple[GaptGit, _CapturedRunner]:
    runner = _CapturedRunner()
    return (
        GaptGit(runner=runner, git_binary="/usr/bin/git"),
        runner,
    )


# ───────────────────────────────────────────────────── dispatch ──


@pytest.mark.asyncio
async def test_unknown_action_rejected(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="rebase"))
    assert exc.value.code == "exec.tool.invalid_input"


@pytest.mark.asyncio
async def test_action_required(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(
            ToolInvocation(name="gapt_git", arguments={}, workspace_root=str(tmp_path))
        )
    assert exc.value.code == "exec.tool.invalid_input"


# ─────────────────────────────────────────────────────── status ──


@pytest.mark.asyncio
async def test_status_uses_short_branch(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.stdout = "## main\n M readme\n"
    result = await tool.execute(_inv(tmp_path, action="status"))
    assert runner.calls[0][0][1:] == ["status", "--short", "--branch"]
    assert "M readme" in result.content


# ────────────────────────────────────────────────────────── log ──


@pytest.mark.asyncio
async def test_log_default_limit(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.stdout = "abc Initial\n"
    await tool.execute(_inv(tmp_path, action="log"))
    assert runner.calls[0][0][2] == "-n20"


@pytest.mark.asyncio
async def test_log_custom_limit(tmp_path: Path) -> None:
    tool, runner = _tool()
    await tool.execute(_inv(tmp_path, action="log", args={"limit": 5}))
    assert runner.calls[0][0][2] == "-n5"


@pytest.mark.asyncio
async def test_log_invalid_limit_rejected(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="log", args={"limit": 0}))
    assert exc.value.code == "exec.tool.invalid_input"


# ────────────────────────────────────────────────────────── diff ──


@pytest.mark.asyncio
async def test_diff_with_paths(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("")
    tool, runner = _tool()
    await tool.execute(_inv(tmp_path, action="diff", args={"paths": ["a.py"]}))
    argv = runner.calls[0][0]
    assert "--" in argv
    assert "a.py" in argv


@pytest.mark.asyncio
async def test_diff_cached_flag(tmp_path: Path) -> None:
    tool, runner = _tool()
    await tool.execute(_inv(tmp_path, action="diff", args={"cached": True}))
    assert "--cached" in runner.calls[0][0]


@pytest.mark.asyncio
async def test_diff_traversal_rejected(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="diff", args={"paths": ["../etc/passwd"]}))
    assert exc.value.code == "exec.tool.access_denied"


# ──────────────────────────────────────────────────── branch / checkout ──


@pytest.mark.asyncio
async def test_branch_show_current(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.stdout = "feat/x\n"
    result = await tool.execute(_inv(tmp_path, action="branch"))
    assert runner.calls[0][0][1:] == ["branch", "--show-current"]
    assert result.content == "feat/x"


@pytest.mark.asyncio
async def test_checkout_create_branch(tmp_path: Path) -> None:
    tool, runner = _tool()
    await tool.execute(_inv(tmp_path, action="checkout", args={"ref": "feat/x", "create": True}))
    argv = runner.calls[0][0]
    assert argv[1:] == ["checkout", "-b", "feat/x"]


@pytest.mark.asyncio
async def test_checkout_missing_ref(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="checkout"))
    assert exc.value.code == "exec.tool.invalid_input"


# ────────────────────────────────────────────────────────── add ──


@pytest.mark.asyncio
async def test_add_paths(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    tool, runner = _tool()
    result = await tool.execute(_inv(tmp_path, action="add", args={"paths": ["a.py", "b.py"]}))
    argv = runner.calls[0][0]
    assert argv[1:4] == ["add", "--", "a.py"]
    assert "b.py" in argv
    assert "2 path(s)" in result.content


@pytest.mark.asyncio
async def test_add_empty_paths(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="add", args={"paths": []}))
    assert exc.value.code == "exec.tool.invalid_input"


@pytest.mark.asyncio
async def test_add_traversal_rejected(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="add", args={"paths": ["../outside.py"]}))
    assert exc.value.code == "exec.tool.access_denied"


# ────────────────────────────────────────────────────────── commit ──


@pytest.mark.asyncio
async def test_commit_appends_coauthor(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.stdout = "[main abc123] feat"
    await tool.execute(_inv(tmp_path, action="commit", args={"message": "feat: x"}))
    msg = runner.calls[0][0][3]
    assert "feat: x" in msg
    assert "Co-Authored-By: Claude Opus 4.7" in msg


@pytest.mark.asyncio
async def test_commit_skips_coauthor_if_already_present(tmp_path: Path) -> None:
    tool, runner = _tool()
    body = "feat: x\n\nCo-Authored-By: alice@example.com\n"
    await tool.execute(_inv(tmp_path, action="commit", args={"message": body}))
    msg = runner.calls[0][0][3]
    # The trailer was already there — we don't double up.
    assert msg.count("Co-Authored-By") == 1


@pytest.mark.asyncio
async def test_commit_empty_message_rejected(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="commit", args={"message": "  "}))
    assert exc.value.code == "exec.tool.invalid_input"


# ────────────────────────────────────────────────────────── push ──


@pytest.mark.asyncio
async def test_push_to_feature_branch(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.stdout = "Everything up-to-date"
    await tool.execute(
        _inv(tmp_path, action="push", args={"branch": "feat/x", "set_upstream": True})
    )
    argv = runner.calls[0][0]
    assert "--set-upstream" in argv
    assert "origin" in argv
    assert "feat/x" in argv


@pytest.mark.asyncio
async def test_push_to_main_is_refused(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="push", args={"branch": "main"}))
    assert exc.value.code == "git.push.protected"


@pytest.mark.asyncio
async def test_push_to_master_is_refused(tmp_path: Path) -> None:
    tool, _ = _tool()
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="push", args={"branch": "master"}))
    assert exc.value.code == "git.push.protected"


@pytest.mark.asyncio
async def test_push_force_with_lease_only(tmp_path: Path) -> None:
    tool, runner = _tool()
    await tool.execute(
        _inv(
            tmp_path,
            action="push",
            args={"branch": "feat/x", "force_with_lease": True},
        )
    )
    argv = runner.calls[0][0]
    assert "--force-with-lease" in argv
    # Plain --force is never emitted.
    assert "--force" not in argv


@pytest.mark.asyncio
async def test_push_failure_code(tmp_path: Path) -> None:
    tool, runner = _tool()
    runner.exit_code = 128
    runner.stderr = "rejected"
    with pytest.raises(ToolError) as exc:
        await tool.execute(_inv(tmp_path, action="push", args={"branch": "feat/x"}))
    assert exc.value.code == "git.push.failed"


@pytest.mark.asyncio
async def test_custom_protected_branches() -> None:
    """A deployment can extend the protected set without code changes."""
    runner = _CapturedRunner()
    tool = GaptGit(
        runner=runner,
        git_binary="/usr/bin/git",
        protected_branches=frozenset({"trunk", "stable"}),
    )
    with pytest.raises(ToolError) as exc:
        await tool.execute(
            ToolInvocation(
                name="gapt_git",
                arguments={"action": "push", "args": {"branch": "trunk"}},
                workspace_root="/tmp",
            )
        )
    assert exc.value.code == "git.push.protected"
