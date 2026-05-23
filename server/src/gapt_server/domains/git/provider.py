"""`GitProvider` protocol + value types.

Cycle 2.6a defines the contract; Cycle 2.6b implements it against
`gh` for GitHub. The interface is intentionally narrow — only the
operations the agent + UI need today:

- ``list_user_repos`` — populate the "register existing repo" UI.
- ``clone``           — initial workspace bootstrap.
- ``fetch`` / ``push``— mid-session sync.
- ``open_pr``         — `gapt_pr` tool target.
- ``get_pr_status``   — sidebar PR status.
- ``list_workflow_runs`` / ``get_workflow_run_logs`` — CI surface.

Implementations operate **inside the sandbox** via the daemon's
``/exec`` endpoint (so the token only lives in the sandbox's askpass
env, never on the host). They do not perform filesystem mutation
themselves — they generate the command vector and ask the daemon to
run it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003  — dataclass annotation resolved at runtime
from enum import StrEnum
from typing import Any, Protocol


class GitOperationError(RuntimeError):
    """Stable code suffix maps to HTTP / audit semantics."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GitRepoSummary:
    full_name: str  # owner/repo
    description: str | None
    private: bool
    default_branch: str
    clone_url: str


@dataclass(frozen=True)
class GitCloneSpec:
    """Inputs for a clone operation. The sandbox-side daemon is the
    runtime caller; this object travels over JSON so all fields are
    primitive."""

    remote_url: str
    branch: str | None = None
    depth: int | None = None  # `None` = full history, `1` = shallow
    target_dir: str = "."  # relative to workspace_root
    submodules: bool = False


@dataclass(frozen=True)
class GitCommitInfo:
    sha: str
    author_name: str
    author_email: str
    subject: str
    timestamp: datetime


@dataclass(frozen=True)
class GitPushSpec:
    branch: str
    remote: str = "origin"
    force_with_lease: bool = False  # plain `--force` is never allowed
    set_upstream: bool = False


@dataclass(frozen=True)
class GitPullRequest:
    number: int
    title: str
    body: str
    head_ref: str
    base_ref: str
    state: str  # "open" | "closed" | "merged"
    url: str
    draft: bool = False


class WorkflowRunStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED_SUCCESS = "completed_success"
    COMPLETED_FAILURE = "completed_failure"
    COMPLETED_CANCELLED = "completed_cancelled"
    COMPLETED_NEUTRAL = "completed_neutral"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkflowRun:
    id: int
    name: str
    head_branch: str
    head_sha: str
    status: WorkflowRunStatus
    html_url: str
    raw: dict[str, Any] = field(default_factory=dict)


class GitProvider(Protocol):
    """The narrow surface every git host implements. M1-E2 ships
    ``GithubProvider``; ``GitlabProvider`` / ``GiteaProvider`` plug in
    later without touching ``gapt_git`` / ``gapt_pr`` callers.
    """

    name: str

    async def list_user_repos(self) -> list[GitRepoSummary]: ...

    async def clone(self, spec: GitCloneSpec) -> None: ...

    async def fetch(self, *, remote: str = "origin") -> None: ...

    async def push(self, spec: GitPushSpec) -> None: ...

    async def open_pr(
        self,
        *,
        title: str,
        body: str,
        base: str,
        head: str,
        draft: bool = False,
    ) -> GitPullRequest: ...

    async def get_pr_status(self, *, number: int) -> GitPullRequest: ...

    async def list_workflow_runs(
        self, *, branch: str | None = None, limit: int = 20
    ) -> list[WorkflowRun]: ...

    async def get_workflow_run_logs(self, *, run_id: int) -> str: ...
