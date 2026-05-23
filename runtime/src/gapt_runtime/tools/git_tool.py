"""``gapt_git`` — sandbox-side git wrapper for the LLM.

Action-dispatched: each ``action`` arg corresponds to a different git
subcommand. The handlers are intentionally narrow — no arbitrary
``--option`` passthrough, so the LLM can't dodge the policy gate by
sneaking in raw flags.

Notable safety properties:

- ``commit`` always appends the Co-Authored-By trailer (matches the
  [[reference_git_identity]] standard).
- ``push`` refuses ``main`` / ``master`` (and other configured
  protected branches) outright — that's the ``git.push.protected``
  policy gate. Cycle 2.9's HookRunner can layer additional checks on
  top of this hard floor.
- ``push --force`` is never representable. ``--force-with-lease`` is
  available behind ``force_with_lease=true``.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from gapt_runtime.tools.protocol import (
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchema,
)
from gapt_runtime.workspace import WorkspaceTraversalError, resolve_under_root

GitRunner = Callable[
    [list[str], str],
    Awaitable[tuple[int, str, str]],
]

_DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "master", "release", "production"})

_DEFAULT_COAUTHOR = "Claude Opus 4.7 (1M context) <noreply@anthropic.com>"


async def _default_runner(argv: list[str], cwd: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout_b, stderr_b = await proc.communicate()
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _ensure_git_binary(override: str | None) -> str:
    if override:
        return override
    which = shutil.which("git")
    if which is None:
        raise ToolError(
            "exec.tool.invalid_input",
            "`git` not on PATH inside sandbox; daemon image is misconfigured",
        )
    return which


class GaptGit:
    name = "gapt_git"
    description = (
        "Run a git operation inside the workspace. Pick one of: "
        "status, log, diff, branch, checkout, add, commit, push."
    )
    schema = ToolSchema(
        properties={
            "action": {
                "type": "string",
                "enum": [
                    "status",
                    "log",
                    "diff",
                    "branch",
                    "checkout",
                    "add",
                    "commit",
                    "push",
                ],
            },
            "args": {
                "type": "object",
                "description": "Action-specific arguments (see action docs).",
            },
        },
        required=("action",),
    )

    def __init__(
        self,
        *,
        runner: GitRunner = _default_runner,
        git_binary: str | None = None,
        coauthor_trailer: str | None = _DEFAULT_COAUTHOR,
        protected_branches: frozenset[str] = _DEFAULT_PROTECTED_BRANCHES,
    ) -> None:
        self._runner = runner
        self._git = git_binary
        self._coauthor = coauthor_trailer
        self._protected = protected_branches

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        action = invocation.arguments.get("action")
        if not isinstance(action, str):
            raise ToolError("exec.tool.invalid_input", "`action` must be a string")
        raw_args = invocation.arguments.get("args", {})
        if not isinstance(raw_args, dict):
            raise ToolError("exec.tool.invalid_input", "`args` must be an object")
        args: dict[str, Any] = raw_args

        handler = _ACTIONS.get(action)
        if handler is None:
            raise ToolError(
                "exec.tool.invalid_input",
                f"unknown gapt_git action: {action!r}",
            )
        return await handler(self, invocation, args)

    # ─────────────────────────────────────── helpers ──

    async def _run(self, argv: list[str], workspace_root: str) -> tuple[int, str, str]:
        bin_path = _ensure_git_binary(self._git)
        return await self._runner([bin_path, *argv], workspace_root)

    async def _git_ok(self, argv: list[str], workspace_root: str, fail_code: str) -> str:
        exit_code, stdout, stderr = await self._run(argv, workspace_root)
        if exit_code != 0:
            raise ToolError(
                fail_code,
                f"`git {' '.join(argv)}` exited {exit_code}: {stderr.strip()[:400]}",
            )
        return stdout


# ─────────────────────────────────────────────────── action handlers ──


async def _do_status(tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    out = await tool._git_ok(
        ["status", "--short", "--branch"], invocation.workspace_root, "exec.tool.crashed"
    )
    return ToolResult(content=out.rstrip(), metadata={"action": "status"})


async def _do_log(tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    limit_raw = args.get("limit", 20)
    if not isinstance(limit_raw, int) or limit_raw < 1 or limit_raw > 200:
        raise ToolError("exec.tool.invalid_input", "`limit` must be int 1..200")
    out = await tool._git_ok(
        ["log", f"-n{limit_raw}", "--oneline", "--decorate"],
        invocation.workspace_root,
        "exec.tool.crashed",
    )
    return ToolResult(
        content=out.rstrip(),
        metadata={"action": "log", "limit": limit_raw},
    )


async def _do_diff(tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    cached = bool(args.get("cached", False))
    paths_raw = args.get("paths", [])
    if not isinstance(paths_raw, list):
        raise ToolError("exec.tool.invalid_input", "`paths` must be a list of strings")
    paths: list[str] = []
    for p in paths_raw:
        if not isinstance(p, str):
            raise ToolError("exec.tool.invalid_input", "`paths[*]` must be strings")
        try:
            resolve_under_root(Path(invocation.workspace_root), p)
        except WorkspaceTraversalError as exc:
            raise ToolError("exec.tool.access_denied", str(exc)) from exc
        paths.append(p)

    argv = ["diff"]
    if cached:
        argv.append("--cached")
    if paths:
        argv.append("--")
        argv.extend(paths)
    out = await tool._git_ok(argv, invocation.workspace_root, "exec.tool.crashed")
    return ToolResult(content=out.rstrip(), metadata={"action": "diff", "cached": cached})


async def _do_branch(tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    out = await tool._git_ok(
        ["branch", "--show-current"],
        invocation.workspace_root,
        "exec.tool.crashed",
    )
    return ToolResult(content=out.rstrip(), metadata={"action": "branch"})


async def _do_checkout(
    tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]
) -> ToolResult:
    ref = args.get("ref")
    if not isinstance(ref, str) or not ref:
        raise ToolError("exec.tool.invalid_input", "`ref` must be a non-empty string")
    create = bool(args.get("create", False))
    argv = ["checkout"]
    if create:
        argv.append("-b")
    argv.append(ref)
    out = await tool._git_ok(argv, invocation.workspace_root, "exec.tool.crashed")
    return ToolResult(
        content=out.strip() or f"checked out {ref}",
        metadata={"action": "checkout", "ref": ref, "create": create},
    )


async def _do_add(tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    paths_raw = args.get("paths")
    if not isinstance(paths_raw, list) or not paths_raw:
        raise ToolError("exec.tool.invalid_input", "`paths` must be a non-empty list")
    for p in paths_raw:
        if not isinstance(p, str):
            raise ToolError("exec.tool.invalid_input", "`paths[*]` must be strings")
        try:
            resolve_under_root(Path(invocation.workspace_root), p)
        except WorkspaceTraversalError as exc:
            raise ToolError("exec.tool.access_denied", str(exc)) from exc
    await tool._git_ok(
        ["add", "--", *paths_raw],
        invocation.workspace_root,
        "exec.tool.crashed",
    )
    return ToolResult(content=f"staged {len(paths_raw)} path(s)", metadata={"action": "add"})


async def _do_commit(tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    message = args.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ToolError("exec.tool.invalid_input", "`message` must be a non-empty string")

    body = message.rstrip()
    if tool._coauthor and "Co-Authored-By" not in body:
        body = f"{body}\n\nCo-Authored-By: {tool._coauthor}\n"

    out = await tool._git_ok(
        ["commit", "-m", body],
        invocation.workspace_root,
        "exec.tool.crashed",
    )
    return ToolResult(content=out.rstrip(), metadata={"action": "commit"})


async def _do_push(tool: GaptGit, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    branch = args.get("branch")
    if not isinstance(branch, str) or not branch:
        raise ToolError("exec.tool.invalid_input", "`branch` must be a non-empty string")
    if branch in tool._protected:
        raise ToolError(
            "git.push.protected",
            f"refusing to push to protected branch {branch!r} from the agent; "
            "user must drive protected pushes themselves",
        )
    remote = args.get("remote", "origin")
    if not isinstance(remote, str):
        raise ToolError("exec.tool.invalid_input", "`remote` must be a string")

    set_upstream = bool(args.get("set_upstream", False))
    force_with_lease = bool(args.get("force_with_lease", False))

    argv = ["push"]
    if set_upstream:
        argv.append("--set-upstream")
    argv.extend([remote, branch])
    if force_with_lease:
        argv.append("--force-with-lease")

    out = await tool._git_ok(argv, invocation.workspace_root, "git.push.failed")
    return ToolResult(
        content=out.rstrip(),
        metadata={
            "action": "push",
            "branch": branch,
            "remote": remote,
            "force_with_lease": force_with_lease,
        },
    )


_ACTIONS: dict[
    str,
    Callable[[GaptGit, ToolInvocation, dict[str, Any]], Awaitable[ToolResult]],
] = {
    "status": _do_status,
    "log": _do_log,
    "diff": _do_diff,
    "branch": _do_branch,
    "checkout": _do_checkout,
    "add": _do_add,
    "commit": _do_commit,
    "push": _do_push,
}
