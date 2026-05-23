"""``gapt_pr`` — sandbox-side `gh pr` wrapper for the LLM.

Three actions:

- ``create``         — open a PR. Returns ``{url, number}``.
- ``review_request`` — add reviewers / labels to an existing PR.
- ``merge``          — merge a PR. Protected base branches refuse
  unless ``confirm_protected=true`` *and* a follow-up user-approval
  step lands (Cycle 2.9 HookRunner wires that).

Same safety posture as ``gapt_git``: action-specific schema only,
no raw ``gh`` flag passthrough. ``--admin`` (which bypasses required
reviews) is never representable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from gapt_runtime.tools.protocol import (
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchema,
)

GhRunner = Callable[
    [list[str], str],
    Awaitable[tuple[int, str, str]],
]

_DEFAULT_PROTECTED_BASES = frozenset({"main", "master", "release", "production"})

_VALID_MERGE_STRATEGIES = frozenset({"merge", "squash", "rebase"})


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


def _ensure_gh_binary(override: str | None) -> str:
    if override:
        return override
    which = shutil.which("gh")
    if which is None:
        raise ToolError(
            "exec.tool.invalid_input",
            "`gh` not on PATH inside sandbox; daemon image is misconfigured",
        )
    return which


class GaptPr:
    name = "gapt_pr"
    description = (
        "Manage a GitHub pull request: create, request reviewers, or merge. "
        "Pass `action` plus action-specific args."
    )
    schema = ToolSchema(
        properties={
            "action": {
                "type": "string",
                "enum": ["create", "review_request", "merge"],
            },
            "args": {"type": "object"},
        },
        required=("action",),
    )

    def __init__(
        self,
        *,
        runner: GhRunner = _default_runner,
        gh_binary: str | None = None,
        protected_bases: frozenset[str] = _DEFAULT_PROTECTED_BASES,
    ) -> None:
        self._runner = runner
        self._gh = gh_binary
        self._protected_bases = protected_bases

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        action = invocation.arguments.get("action")
        if not isinstance(action, str):
            raise ToolError("exec.tool.invalid_input", "`action` must be a string")
        raw_args = invocation.arguments.get("args", {})
        if not isinstance(raw_args, dict):
            raise ToolError("exec.tool.invalid_input", "`args` must be an object")

        handler = _ACTIONS.get(action)
        if handler is None:
            raise ToolError(
                "exec.tool.invalid_input",
                f"unknown gapt_pr action: {action!r}",
            )
        return await handler(self, invocation, raw_args)

    async def _run(self, argv: list[str], cwd: str) -> tuple[int, str, str]:
        bin_path = _ensure_gh_binary(self._gh)
        return await self._runner([bin_path, *argv], cwd)

    async def _gh_ok(self, argv: list[str], cwd: str, fail_code: str) -> str:
        exit_code, stdout, stderr = await self._run(argv, cwd)
        if exit_code != 0:
            raise ToolError(
                fail_code,
                f"`gh {' '.join(argv)}` exited {exit_code}: {stderr.strip()[:400]}",
            )
        return stdout


# ──────────────────────────────────────────────── action handlers ──


async def _do_create(tool: GaptPr, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    title = args.get("title")
    body = args.get("body", "")
    base = args.get("base")
    head = args.get("head")
    draft = bool(args.get("draft", False))

    if not isinstance(title, str) or not title.strip():
        raise ToolError("exec.tool.invalid_input", "`title` must be a non-empty string")
    if not isinstance(body, str):
        raise ToolError("exec.tool.invalid_input", "`body` must be a string")
    if not isinstance(base, str) or not base:
        raise ToolError("exec.tool.invalid_input", "`base` must be a non-empty string")
    if not isinstance(head, str) or not head:
        raise ToolError("exec.tool.invalid_input", "`head` must be a non-empty string")

    argv = [
        "pr",
        "create",
        "--title",
        title,
        "--body",
        body,
        "--base",
        base,
        "--head",
        head,
    ]
    if draft:
        argv.append("--draft")

    stdout = await tool._gh_ok(argv, invocation.workspace_root, "git.pr_create_failed")
    url = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if not url.startswith("http"):
        raise ToolError(
            "git.pr_create_unexpected_output",
            f"`gh pr create` returned: {stdout[:200]}",
        )
    try:
        number = int(url.rsplit("/", 1)[-1])
    except ValueError as exc:
        raise ToolError(
            "git.pr_create_unexpected_output",
            f"could not parse PR number from {url!r}",
        ) from exc

    return ToolResult(
        content=url,
        metadata={"action": "create", "number": number, "url": url, "draft": draft},
    )


async def _do_review_request(
    tool: GaptPr, invocation: ToolInvocation, args: dict[str, Any]
) -> ToolResult:
    number = args.get("number")
    if not isinstance(number, int) or number < 1:
        raise ToolError("exec.tool.invalid_input", "`number` must be a positive int")

    reviewers = args.get("reviewers", [])
    labels = args.get("labels", [])
    if not isinstance(reviewers, list) or not isinstance(labels, list):
        raise ToolError("exec.tool.invalid_input", "`reviewers` and `labels` must be lists")
    for v in [*reviewers, *labels]:
        if not isinstance(v, str) or not v:
            raise ToolError(
                "exec.tool.invalid_input",
                "every reviewer / label entry must be a non-empty string",
            )
    if not reviewers and not labels:
        raise ToolError(
            "exec.tool.invalid_input",
            "pass at least one of `reviewers` or `labels`",
        )

    argv = ["pr", "edit", str(number)]
    if reviewers:
        argv.extend(["--add-reviewer", ",".join(reviewers)])
    if labels:
        argv.extend(["--add-label", ",".join(labels)])
    await tool._gh_ok(argv, invocation.workspace_root, "git.pr_review_request_failed")

    return ToolResult(
        content=f"updated PR #{number}",
        metadata={
            "action": "review_request",
            "number": number,
            "reviewers": reviewers,
            "labels": labels,
        },
    )


async def _do_merge(tool: GaptPr, invocation: ToolInvocation, args: dict[str, Any]) -> ToolResult:
    number = args.get("number")
    if not isinstance(number, int) or number < 1:
        raise ToolError("exec.tool.invalid_input", "`number` must be a positive int")

    strategy = args.get("strategy", "squash")
    if strategy not in _VALID_MERGE_STRATEGIES:
        raise ToolError(
            "exec.tool.invalid_input",
            f"`strategy` must be one of {sorted(_VALID_MERGE_STRATEGIES)}",
        )

    confirm_protected = bool(args.get("confirm_protected", False))

    # Look up base ref so we can refuse a protected merge.
    view_argv = ["pr", "view", str(number), "--json", "baseRefName,headRefName"]
    info_raw = await tool._gh_ok(view_argv, invocation.workspace_root, "git.pr_view_failed")
    try:
        info = json.loads(info_raw)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "git.pr_view_failed",
            f"`gh pr view #{number}` returned non-JSON: {info_raw[:200]}",
        ) from exc
    base = str(info.get("baseRefName", ""))
    if base in tool._protected_bases and not confirm_protected:
        raise ToolError(
            "git.pr.merge_protected",
            f"PR #{number} targets protected base {base!r}; "
            "set `confirm_protected=true` after the user reviews it",
        )

    argv = ["pr", "merge", str(number), f"--{strategy}", "--delete-branch"]
    await tool._gh_ok(argv, invocation.workspace_root, "git.pr_merge_failed")

    return ToolResult(
        content=f"merged PR #{number} via {strategy}",
        metadata={
            "action": "merge",
            "number": number,
            "strategy": strategy,
            "base": base,
            "confirm_protected": confirm_protected,
        },
    )


_ACTIONS: dict[
    str,
    Callable[[GaptPr, ToolInvocation, dict[str, Any]], Awaitable[ToolResult]],
] = {
    "create": _do_create,
    "review_request": _do_review_request,
    "merge": _do_merge,
}
