"""Git domain — D3.

`GitProvider` is the protocol every concrete provider satisfies.
M1-E2 ships:

- Cycle 2.6a (this PR) — protocol + askpass helper + `GitOperationError`.
- Cycle 2.6b           — `GithubProvider` driving `gh` CLI as subprocess.

The askpass dance is load-bearing for safety: tokens never get
persisted on the host filesystem. The control plane mints a short-lived
askpass token, the sandbox-side `gapt-askpass` helper reads the env
var and prints to stdout — git/gh consume it once, then we discard.
"""

from gapt_server.domains.git.askpass import (
    AskpassError,
    AskpassToken,
    AskpassTokenStore,
)
from gapt_server.domains.git.github_provider import GithubProvider
from gapt_server.domains.git.provider import (
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

__all__ = [
    "AskpassError",
    "AskpassToken",
    "AskpassTokenStore",
    "GitCloneSpec",
    "GitCommitInfo",
    "GitOperationError",
    "GitProvider",
    "GitPullRequest",
    "GitPushSpec",
    "GitRepoSummary",
    "GithubProvider",
    "WorkflowRun",
    "WorkflowRunStatus",
]
