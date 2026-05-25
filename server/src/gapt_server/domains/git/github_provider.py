"""`GithubProvider` — `gh` CLI subprocess driver.

Used by the *control plane* for repo listing / PR status / workflow
status. Sandbox-side git operations (clone / commit / push) go
through the daemon's ``/exec`` + the askpass dance in Cycle 2.7.

Every gh invocation receives ``GH_TOKEN`` via env (the gh CLI's
documented way to consume an OAuth token). The token is the plaintext
from the askpass store — we mint one per call and discard.

JSON-mode (`--json`) is used wherever gh supports it so we never parse
free-form text.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from gapt_server.domains.git.provider import (
    GitCloneSpec,
    GitOperationError,
    GitPullRequest,
    GitPushSpec,
    GitRepoSummary,
    WorkflowRun,
    WorkflowRunStatus,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger(__name__)


# Test seam: replace at module level (or per-instance) so tests don't
# spawn real subprocesses.
ProcRunner = "Callable[[list[str], dict[str, str], str | None], Awaitable[tuple[int, str, str]]]"


async def _default_runner(
    argv: list[str], env: dict[str, str], cwd: str | None
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    stdout_b, stderr_b = await proc.communicate()
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


@dataclass
class GithubProvider:
    """Minimal but production-shaped GitHub driver."""

    repo: str  # owner/name; required for PR + workflow ops
    token: str  # access token issued via Device Flow
    runner: Callable[
        [list[str], dict[str, str], str | None],
        Awaitable[tuple[int, str, str]],
    ] = _default_runner
    gh_binary: str | None = None  # absolute path; falls back to PATH
    name: str = "github"

    def _resolve_binary(self) -> str:
        if self.gh_binary is not None:
            return self.gh_binary
        which = shutil.which("gh")
        if which is None:
            raise GitOperationError(
                "git.gh_not_found",
                "`gh` CLI not on PATH; install GitHub CLI or set gh_binary",
            )
        return which

    def _env(self) -> dict[str, str]:
        # Inherit the existing process env so HTTPS_PROXY / locale work,
        # then override the auth + non-interactivity knobs.
        env = dict(os.environ)
        env["GH_TOKEN"] = self.token
        env["GH_PROMPT_DISABLED"] = "true"
        env["NO_COLOR"] = "1"
        return env

    async def _run(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
    ) -> tuple[str, str]:
        bin_path = self._resolve_binary()
        full_argv = [bin_path, *argv]
        exit_code, stdout, stderr = await self.runner(full_argv, self._env(), cwd)
        if exit_code != 0:
            raise GitOperationError(
                "git.gh_failed",
                f"`gh {' '.join(argv)}` exited {exit_code}: {stderr.strip()[:400]}",
            )
        return stdout, stderr

    async def _run_json(self, argv: list[str]) -> Any:
        stdout, _ = await self._run(argv)
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise GitOperationError(
                "git.gh_malformed_json",
                f"`gh {' '.join(argv)}` produced non-JSON output: {stdout[:200]}",
            ) from exc

    # ───────────────────────────────────────────────────── repos ──

    async def list_user_repos(self) -> list[GitRepoSummary]:
        data = await self._run_json(
            [
                "repo",
                "list",
                "--limit",
                "100",
                "--json",
                "nameWithOwner,description,isPrivate,defaultBranchRef,url",
            ]
        )
        return [
            GitRepoSummary(
                full_name=item["nameWithOwner"],
                description=item.get("description") or None,
                private=bool(item.get("isPrivate", False)),
                default_branch=(item.get("defaultBranchRef") or {}).get("name", "main"),
                clone_url=item.get("url", "") + ".git",
            )
            for item in data
        ]

    # ─────────────────────────────────────────────────── clone/git ──

    async def clone(self, spec: GitCloneSpec) -> None:
        argv: list[str] = ["repo", "clone", spec.remote_url, spec.target_dir, "--"]
        if spec.branch:
            argv.extend(["--branch", spec.branch])
        if spec.depth:
            argv.extend(["--depth", str(spec.depth)])
        if not spec.submodules:
            argv.append("--no-recurse-submodules")
        await self._run(argv)

    async def fetch(self, *, remote: str = "origin") -> None:
        # `gh` itself doesn't fetch — defer to git directly.
        bin_path = shutil.which("git")
        if bin_path is None:
            raise GitOperationError("git.gh_not_found", "`git` not on PATH")
        exit_code, _, stderr = await self.runner([bin_path, "fetch", remote], self._env(), None)
        if exit_code != 0:
            raise GitOperationError(
                "git.fetch_failed",
                f"`git fetch {remote}` exited {exit_code}: {stderr.strip()[:200]}",
            )

    async def push(self, spec: GitPushSpec) -> None:
        bin_path = shutil.which("git")
        if bin_path is None:
            raise GitOperationError("git.gh_not_found", "`git` not on PATH")
        argv = [bin_path, "push"]
        if spec.set_upstream:
            argv.append("--set-upstream")
        argv.extend([spec.remote, spec.branch])
        if spec.force_with_lease:
            argv.append("--force-with-lease")
        exit_code, _, stderr = await self.runner(argv, self._env(), None)
        if exit_code != 0:
            raise GitOperationError(
                "git.push_failed",
                f"`git push` exited {exit_code}: {stderr.strip()[:200]}",
            )

    # ──────────────────────────────────────────────────────── PRs ──

    async def open_pr(
        self,
        *,
        title: str,
        body: str,
        base: str,
        head: str,
        draft: bool = False,
    ) -> GitPullRequest:
        argv = [
            "pr",
            "create",
            "--repo",
            self.repo,
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
        stdout, _ = await self._run(argv)
        # `gh pr create` prints the URL on its last line.
        url = stdout.strip().splitlines()[-1] if stdout.strip() else ""
        if not url.startswith("http"):
            raise GitOperationError(
                "git.pr_create_unexpected_output",
                f"`gh pr create` returned: {stdout[:200]}",
            )
        # Resolve number by re-querying — single source of truth.
        try:
            number = int(url.rsplit("/", 1)[-1])
        except ValueError as exc:
            raise GitOperationError(
                "git.pr_create_unexpected_output",
                f"could not parse PR number from {url!r}",
            ) from exc
        return await self.get_pr_status(number=number)

    async def get_pr_status(self, *, number: int) -> GitPullRequest:
        data = await self._run_json(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                self.repo,
                "--json",
                "number,title,body,headRefName,baseRefName,state,url,isDraft",
            ]
        )
        state_raw = str(data.get("state", "OPEN")).lower()
        # gh prints UPPER for state; "MERGED" → "merged".
        return GitPullRequest(
            number=int(data["number"]),
            title=data.get("title", ""),
            body=data.get("body", "") or "",
            head_ref=data.get("headRefName", ""),
            base_ref=data.get("baseRefName", ""),
            state=state_raw,
            url=data.get("url", ""),
            draft=bool(data.get("isDraft", False)),
        )

    # ──────────────────────────────────────────────────── workflows ──

    async def list_workflow_runs(
        self, *, branch: str | None = None, limit: int = 20
    ) -> list[WorkflowRun]:
        argv = [
            "run",
            "list",
            "--repo",
            self.repo,
            "--limit",
            str(limit),
            "--json",
            "databaseId,displayTitle,headBranch,headSha,status,conclusion,url",
        ]
        if branch:
            argv.extend(["--branch", branch])
        data = await self._run_json(argv)
        return [self._row_to_workflow_run(row) for row in data]

    async def get_workflow_run_logs(self, *, run_id: int) -> str:
        stdout, _ = await self._run(["run", "view", str(run_id), "--repo", self.repo, "--log"])
        return stdout

    async def rerun_workflow_run(self, *, run_id: int, failed_only: bool = False) -> None:
        """Trigger a re-run of `run_id`. `failed_only=True` reruns
        just the failed jobs (`gh run rerun --failed`); otherwise the
        whole workflow. The PAT needs the `workflow` scope — without
        it gh returns an error which we surface unchanged as
        `git.gh_failed`."""
        argv = ["run", "rerun", str(run_id), "--repo", self.repo]
        if failed_only:
            argv.append("--failed")
        await self._run(argv)

    @staticmethod
    def _row_to_workflow_run(row: dict[str, Any]) -> WorkflowRun:
        status_str = str(row.get("status", "")).lower()
        conclusion = str(row.get("conclusion", "") or "").lower()
        status = _classify_workflow(status_str, conclusion)
        return WorkflowRun(
            id=int(row["databaseId"]),
            name=row.get("displayTitle", ""),
            head_branch=row.get("headBranch", ""),
            head_sha=row.get("headSha", ""),
            status=status,
            html_url=row.get("url", ""),
            raw=row,
        )


# ─────────────────────────────────────────────── helpers ──


def _classify_workflow(status: str, conclusion: str) -> WorkflowRunStatus:  # noqa: PLR0911
    if status == "queued":
        return WorkflowRunStatus.QUEUED
    if status == "in_progress":
        return WorkflowRunStatus.IN_PROGRESS
    if status == "completed":
        if conclusion == "success":
            return WorkflowRunStatus.COMPLETED_SUCCESS
        if conclusion == "failure":
            return WorkflowRunStatus.COMPLETED_FAILURE
        if conclusion == "cancelled":
            return WorkflowRunStatus.COMPLETED_CANCELLED
        if conclusion == "neutral":
            return WorkflowRunStatus.COMPLETED_NEUTRAL
    return WorkflowRunStatus.UNKNOWN


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
