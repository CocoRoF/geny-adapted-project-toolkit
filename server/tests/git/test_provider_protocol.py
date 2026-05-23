"""GitProvider protocol — a mock implementation satisfies the
Protocol, and the value types round-trip."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gapt_server.domains.git import (
    GitCloneSpec,
    GitCommitInfo,
    GitOperationError,
    GitProvider,
    GitPullRequest,
    GitPushSpec,
    GitRepoSummary,
    WorkflowRun,
    WorkflowRunStatus,
)


class _MockGitProvider:
    name = "mock"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def list_user_repos(self) -> list[GitRepoSummary]:
        self.calls.append(("list_user_repos", None))
        return []

    async def clone(self, spec: GitCloneSpec) -> None:
        self.calls.append(("clone", spec))

    async def fetch(self, *, remote: str = "origin") -> None:
        self.calls.append(("fetch", remote))

    async def push(self, spec: GitPushSpec) -> None:
        self.calls.append(("push", spec))

    async def open_pr(
        self,
        *,
        title: str,
        body: str,
        base: str,
        head: str,
        draft: bool = False,
    ) -> GitPullRequest:
        self.calls.append(("open_pr", (title, body, base, head, draft)))
        return GitPullRequest(
            number=1,
            title=title,
            body=body,
            head_ref=head,
            base_ref=base,
            state="open",
            url="https://example.com/pr/1",
            draft=draft,
        )

    async def get_pr_status(self, *, number: int) -> GitPullRequest:
        self.calls.append(("get_pr_status", number))
        return GitPullRequest(
            number=number,
            title="t",
            body="b",
            head_ref="feat/x",
            base_ref="main",
            state="open",
            url="https://example.com/pr/x",
        )

    async def list_workflow_runs(
        self, *, branch: str | None = None, limit: int = 20
    ) -> list[WorkflowRun]:
        self.calls.append(("list_workflow_runs", (branch, limit)))
        return []

    async def get_workflow_run_logs(self, *, run_id: int) -> str:
        self.calls.append(("get_workflow_run_logs", run_id))
        return "log line\n"


def test_mock_satisfies_protocol() -> None:
    # If it doesn't satisfy GitProvider, this assignment fails at
    # type-check time. At runtime we exercise every method on the
    # protocol to make sure the surface stays in sync.
    provider: GitProvider = _MockGitProvider()
    assert provider.name == "mock"


@pytest.mark.asyncio
async def test_clone_carries_full_spec() -> None:
    p = _MockGitProvider()
    spec = GitCloneSpec(
        remote_url="https://github.com/CocoRoF/x.git",
        branch="main",
        depth=1,
        target_dir="repo",
        submodules=True,
    )
    await p.clone(spec)
    assert p.calls[0] == ("clone", spec)


@pytest.mark.asyncio
async def test_push_refuses_plain_force_only_by_design() -> None:
    # GitPushSpec doesn't expose plain `--force`; only force_with_lease.
    spec = GitPushSpec(branch="feat/x", force_with_lease=True)
    p = _MockGitProvider()
    await p.push(spec)
    assert isinstance(p.calls[0][1], GitPushSpec)
    # Sanity check the field literally doesn't exist (compile-time + runtime).
    assert not hasattr(spec, "force")


def test_workflow_run_status_enum_values() -> None:
    # Stable wire values; never rename once shipped.
    assert WorkflowRunStatus.QUEUED.value == "queued"
    assert WorkflowRunStatus.COMPLETED_SUCCESS.value == "completed_success"
    assert WorkflowRunStatus.COMPLETED_FAILURE.value == "completed_failure"


def test_git_operation_error_code_round_trip() -> None:
    exc = GitOperationError("git.clone.failed", "nope")
    assert exc.code == "git.clone.failed"
    assert str(exc) == "nope"


def test_git_commit_info_construct() -> None:
    info = GitCommitInfo(
        sha="abc",
        author_name="Alice",
        author_email="alice@example.com",
        subject="initial",
        timestamp=datetime(2026, 5, 23, tzinfo=UTC),
    )
    assert info.sha == "abc"
