"""Workspace-level git operations: status / commit / push / PR.

All run *inside* the workspace's docker sandbox via
`WorkspaceSandbox.exec()` — the host's git config and the user's
home directory are never exposed to the workspace's container. The
workspace clone already has the right remote configured (set at
clone time), and the in-container git inherits the bind-mounted
worktree.

Auth for `push` + PR creation: the same `github_token` that workspace
clone uses (read from the project's `git_auth_secret_ref` in the
Secret Vault). Tokens are written to a one-shot `~/.git-credentials`
inside the container for the duration of the operation, then wiped.
For PR creation we use `gh auth login --with-token` against the same
token.
"""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    get_container,
    get_db_session,
)
from gapt_server.db import enums, models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.secrets.vault import SecretVault, SecretVaultError
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.secrets import get_vault

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.container import AppContainer
    from gapt_server.domains.workspace_sandbox import WorkspaceSandbox


router = APIRouter(prefix="/_gapt/api/workspaces", tags=["git"])


# ─── helpers ────────────────────────────────────────────────────────


async def _resolve_workspace(
    db: AsyncSession, *, workspace_id: str, user: AdminPrincipal
) -> models.Workspace:
    # Single-admin model — no membership check; the Depends gate
    # already authenticated the caller.
    _ = user
    ws = (
        await db.execute(
            select(models.Workspace).where(models.Workspace.id == workspace_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": workspace_id},
        )
    if ws.status != enums.WorkspaceStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "workspace.not_running", "reason": ws.status.value},
        )
    return ws


async def _read_github_token(
    db: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
    vault: SecretVault,
) -> str | None:
    """Resolve the project's GitHub token from its
    `git_auth_secret_ref`. Returns None when no secret is bound —
    push/PR endpoints translate that into a clear 412."""
    project = (
        await db.execute(select(models.Project).where(models.Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or not project.git_auth_secret_ref:
        return None
    try:
        return await vault.read(
            db,
            secret_id=project.git_auth_secret_ref,
            purpose="workspace.git",
            actor_id=actor_id,
        )
    except (SecretVaultError, Exception):
        return None


def _is_gapt_managed_path(path: str) -> bool:
    """`.gapt/` is GAPT's runtime scratch dir inside every workspace —
    service log tails, session caches, etc. The user never writes
    there; we never want them to see (or stage) any of it. Single
    check used by both `git status` and the diff endpoint so the UI
    is consistent across both surfaces."""
    return path.startswith(".gapt/") or path == ".gapt"


async def _git_exec(
    sandbox: WorkspaceSandbox,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_s: float = 30.0,
) -> tuple[int, str, str]:
    """Run `git <argv>` in `/workspace`. Returns (rc, stdout, stderr).
    We never let git's password prompt block — `GIT_TERMINAL_PROMPT=0`
    + `GIT_ASKPASS=/bin/true` ensure failed auth turns into a real
    error code instead of hanging."""
    full_env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
        "GIT_AUTHOR_NAME": "GAPT",
        "GIT_AUTHOR_EMAIL": "gapt@hrletsgo.me",
        "GIT_COMMITTER_NAME": "GAPT",
        "GIT_COMMITTER_EMAIL": "gapt@hrletsgo.me",
    }
    if env:
        full_env.update(env)
    rc, out, err = await sandbox.exec(
        ["git", *argv],
        env=full_env,
        cwd="/workspace",
        timeout_s=timeout_s,
    )
    return rc, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


# ─── status ─────────────────────────────────────────────────────────


class GitStatusEntry(BaseModel):
    path: str
    # Two-letter porcelain code. First char = index, second = worktree.
    # We surface as-is so the UI can render "M", "??", "A", "D".
    status: str


class GitStatusResponse(BaseModel):
    branch: str | None
    upstream: str | None
    ahead: int = 0
    behind: int = 0
    entries: list[GitStatusEntry] = Field(default_factory=list)
    # Last 10 commits on the current branch — used by the commit
    # panel to show "what's already here" before suggesting a message.
    recent_commits: list[dict[str, str]] = Field(default_factory=list)


@router.get(
    "/{workspace_id}/git/status", response_model=GitStatusResponse
)
async def git_status(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitStatusResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()

    # branch + upstream tracking — `--porcelain=v2 -b` lines start
    # with `# branch.{head,upstream,ab}`. Then file rows.
    rc, out, err = await _git_exec(
        sandbox, ["status", "--porcelain=v2", "-b", "--untracked-files=all"]
    )
    if rc != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "git.status_failed", "reason": err.strip()[:400]},
        )

    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    entries: list[GitStatusEntry] = []
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head ") :].strip() or None
        elif line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream ") :].strip() or None
        elif line.startswith("# branch.ab "):
            # `# branch.ab +1 -2`
            parts = line[len("# branch.ab ") :].split()
            for p in parts:
                try:
                    val = int(p)
                except ValueError:
                    continue
                if p.startswith("+"):
                    ahead = val
                elif p.startswith("-"):
                    behind = abs(val)
        elif line.startswith("1 ") or line.startswith("2 "):
            # `1 XY ...path` (XY is the 2-char status)
            cols = line.split(" ", 8)
            if len(cols) >= 9:
                entries.append(GitStatusEntry(status=cols[1], path=cols[8]))
        elif line.startswith("? "):
            # Untracked: `? path/to/file`
            entries.append(GitStatusEntry(status="??", path=line[2:].strip()))

    # Strip GAPT-managed runtime files from the changes list. These
    # live under `.gapt/` (service stdout/stderr logs, session caches,
    # etc.) and have no business showing up in the Source Control
    # panel — they're noise that clutters the operator's commit flow
    # and tempts them to accidentally stage `.gapt/services/dev.log`.
    # Filter is applied AFTER `git status` rather than via
    # `.gitignore` so we don't mutate the user's repo on workspace
    # boot — `.gitignore` belongs to the user, GAPT-internal paths
    # are GAPT's concern alone.
    entries = [e for e in entries if not _is_gapt_managed_path(e.path)]

    rc, out, _ = await _git_exec(
        sandbox,
        ["log", "-10", "--pretty=%h%x09%s", "--no-color"],
        timeout_s=10.0,
    )
    recent: list[dict[str, str]] = []
    if rc == 0:
        for line in out.splitlines():
            if "\t" not in line:
                continue
            sha, msg = line.split("\t", 1)
            recent.append({"sha": sha.strip(), "message": msg.strip()})

    return GitStatusResponse(
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        entries=entries,
        recent_commits=recent,
    )


# ─── diff ───────────────────────────────────────────────────────────


class GitDiffResponse(BaseModel):
    path: str
    diff: str


@router.get(
    "/{workspace_id}/git/diff", response_model=GitDiffResponse
)
async def git_diff(
    workspace_id: str,
    path: str,
    staged: bool = False,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitDiffResponse:
    """Unified diff for one path. `staged=true` returns the index-vs-
    HEAD diff (for files the user already staged); otherwise the
    worktree-vs-HEAD diff (the common case)."""
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    argv = ["diff", "--no-color", "--unified=3"]
    if staged:
        argv.append("--staged")
    argv.extend(["--", path])
    rc, out, err = await _git_exec(sandbox, argv, timeout_s=10.0)
    if rc != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "git.diff_failed", "reason": err.strip()[:400]},
        )
    return GitDiffResponse(path=path, diff=out)


# ─── commit ─────────────────────────────────────────────────────────


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    # Specific paths to add. Empty list = stage every modified +
    # untracked file (`git add -A`). The wizard sends paths the
    # user explicitly checked.
    paths: list[str] = Field(default_factory=list)


class GitCommitResponse(BaseModel):
    sha: str
    branch: str | None
    log: str


@router.post(
    "/{workspace_id}/git/commit", response_model=GitCommitResponse
)
async def git_commit(
    workspace_id: str,
    payload: GitCommitRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitCommitResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()

    # Stage. `git add -A` for the "everything" case; otherwise pass
    # specific paths to keep commits scoped.
    add_argv: list[str] = ["add"]
    if payload.paths:
        # `git add -- <paths>` — `--` so paths starting with `-` are
        # never treated as flags.
        add_argv.extend(["--", *payload.paths])
    else:
        add_argv.append("-A")
    rc, _, err = await _git_exec(sandbox, add_argv, timeout_s=30.0)
    if rc != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "git.add_failed", "reason": err.strip()[:400]},
        )

    # Commit. Build trailer via the admin id so the audit log can
    # correlate this commit back to the GAPT operator. Single-admin
    # model — no email field on the principal.
    user_name = user.id or "gapt"
    user_email = f"{user_name}@gapt.local"
    commit_env = {
        "GIT_AUTHOR_NAME": user_name,
        "GIT_AUTHOR_EMAIL": user_email,
        "GIT_COMMITTER_NAME": user_name,
        "GIT_COMMITTER_EMAIL": user_email,
    }
    rc, out, err = await _git_exec(
        sandbox,
        ["commit", "-m", payload.message],
        env=commit_env,
        timeout_s=30.0,
    )
    if rc != 0:
        # "nothing to commit" is rc=1 in git — distinguish from a
        # real failure so the UI can render a nice empty-commit notice.
        combined = (out + err).lower()
        if "nothing to commit" in combined or "no changes added" in combined:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "git.nothing_to_commit", "reason": (out + err).strip()[:200]},
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "git.commit_failed", "reason": err.strip()[:400]},
        )

    # Capture the SHA + branch the commit landed on.
    rc, sha_out, _ = await _git_exec(sandbox, ["rev-parse", "--short", "HEAD"])
    rc, branch_out, _ = await _git_exec(sandbox, ["rev-parse", "--abbrev-ref", "HEAD"])
    return GitCommitResponse(
        sha=sha_out.strip(),
        branch=branch_out.strip() or None,
        log=out,
    )


# ─── push ───────────────────────────────────────────────────────────


class GitPushRequest(BaseModel):
    # When None, pushes the current branch to origin/<branch>.
    branch: str | None = Field(default=None, max_length=200)
    # Force-with-lease only — never raw `--force`. The policy file
    # rejects raw force pushes against tracked branches, and the
    # endpoint enforces a hard floor.
    force_with_lease: bool = False


class GitPushResponse(BaseModel):
    branch: str | None
    log: str


@router.post(
    "/{workspace_id}/git/push", response_model=GitPushResponse
)
async def git_push(
    workspace_id: str,
    payload: GitPushRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> GitPushResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    token = await _read_github_token(
        db, project_id=ws.project_id, actor_id=user.id, vault=vault
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "git.no_token",
                "reason": "project has no git_auth_secret_ref bound to a github token",
            },
        )

    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()

    branch = payload.branch
    if branch is None:
        rc, out, _ = await _git_exec(sandbox, ["rev-parse", "--abbrev-ref", "HEAD"])
        if rc == 0:
            branch = out.strip()
    if not branch or branch == "HEAD":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "git.detached_head", "reason": "no branch to push"},
        )

    # Auth via short-lived credential helper. We inject the token
    # into the env, then point `credential.helper` at a one-shot
    # script that echoes it. Resets the helper after push so the
    # token doesn't linger in git config.
    push_argv: list[str] = ["push", "origin", f"HEAD:{branch}"]
    if payload.force_with_lease:
        push_argv.append("--force-with-lease")

    # Encode token as `username:password` for https remote — GitHub
    # accepts the PAT as the password with any non-empty username.
    cred_helper = "!f() { echo username=x-access-token; echo password=$GAPT_GITHUB_TOKEN; }; f"
    rc, out, err = await _git_exec(
        sandbox,
        ["-c", f"credential.helper={cred_helper}", *push_argv],
        env={"GAPT_GITHUB_TOKEN": token},
        timeout_s=120.0,
    )
    if rc != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "git.push_failed", "reason": err.strip()[:400]},
        )
    return GitPushResponse(branch=branch, log=(out + err).strip())


# ─── PR creation ─────────────────────────────────────────────────────


class CreatePrRequest(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    body: str = Field(default="", max_length=20000)
    base: str = Field(default="main", max_length=200)
    head: str | None = Field(default=None, max_length=200)
    draft: bool = False


class CreatePrResponse(BaseModel):
    url: str
    number: int
    log: str


@router.post(
    "/{workspace_id}/git/create-pr", response_model=CreatePrResponse
)
async def create_pr(
    workspace_id: str,
    payload: CreatePrRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> CreatePrResponse:
    """Create a GitHub PR using `gh pr create` inside the sandbox.

    Requires a `gh` binary in the workspace image (the
    `gapt-workspace` image ships it via apt) and a project-scoped
    github token (same one used for push).
    """
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    token = await _read_github_token(
        db, project_id=ws.project_id, actor_id=user.id, vault=vault
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "git.no_token", "reason": "no github token bound"},
        )
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()

    head_branch = payload.head
    if head_branch is None:
        rc, out, _ = await _git_exec(sandbox, ["rev-parse", "--abbrev-ref", "HEAD"])
        if rc == 0:
            head_branch = out.strip()
    if not head_branch or head_branch == "HEAD":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "git.detached_head", "reason": "no branch for PR"},
        )

    argv = [
        "pr",
        "create",
        "--base",
        payload.base,
        "--head",
        head_branch,
        "--title",
        payload.title,
        "--body",
        payload.body or "",
    ]
    if payload.draft:
        argv.append("--draft")

    rc, out, err = await sandbox.exec(
        ["gh", *argv],
        env={"GH_TOKEN": token, "GITHUB_TOKEN": token},
        cwd="/workspace",
        timeout_s=60.0,
    )
    stdout = out.decode("utf-8", errors="replace").strip()
    stderr = err.decode("utf-8", errors="replace").strip()
    if rc != 0:
        # `already exists` is a soft conflict — return the existing
        # PR URL the user probably wants.
        if "already exists" in stderr.lower() or "already exists" in stdout.lower():
            # Try `gh pr view --json url,number --jq` to recover URL.
            rc2, out2, _ = await sandbox.exec(
                ["gh", "pr", "view", head_branch, "--json", "url,number"],
                env={"GH_TOKEN": token, "GITHUB_TOKEN": token},
                cwd="/workspace",
                timeout_s=20.0,
            )
            if rc2 == 0:
                try:
                    parsed = json.loads(out2.decode("utf-8"))
                    return CreatePrResponse(
                        url=parsed["url"],
                        number=int(parsed["number"]),
                        log="(PR already exists for this branch)",
                    )
                except Exception:
                    pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "git.pr_create_failed", "reason": stderr[:400] or stdout[:400]},
        )

    # `gh pr create` prints the URL on stdout's last line. Parse.
    url = stdout.splitlines()[-1].strip() if stdout else ""
    if not url.startswith("http"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "git.pr_create_unparseable",
                "reason": stdout[:400] or stderr[:400],
            },
        )
    number = 0
    # The URL ends with `/pull/<number>`.
    try:
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        pass
    return CreatePrResponse(url=url, number=number, log=stdout)


# ─── fetch / pull / sync / discard ──────────────────────────────────


class GitSyncResponse(BaseModel):
    """Outcome of a fetch / pull / sync run. `actions` lists the
    sub-operations the server performed (`fetch`, `pull`, `push`) so
    the UI can show "Synced — fetched, pulled 3 commits, pushed 2"
    without a second status round-trip."""

    ok: bool
    actions: list[str]
    ahead: int = 0
    behind: int = 0
    output: str = ""
    error: str | None = None


async def _git_fetch(sandbox: WorkspaceSandbox, token: str | None) -> tuple[int, str]:
    """`git fetch origin` — refreshes remote-tracking refs without
    touching the worktree. Token (when supplied) is injected via the
    same `http.extraHeader` mechanism the clone path uses so private
    repos work transparently."""
    env = {}
    argv: list[str] = ["fetch", "origin", "--prune"]
    if token:
        # Embed the Basic auth via `-c http.extraHeader=...`. Same
        # pattern as `_default_clone_runner_with_creds` in the
        # workspace boot path — keeps the token off disk and out of
        # the URL.
        import base64  # noqa: PLC0415

        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        argv = [
            "-c",
            f"http.extraHeader=Authorization: Basic {basic}",
            *argv,
        ]
    rc, out, err = await _git_exec(sandbox, argv, env=env, timeout_s=60.0)
    return rc, (out + err).strip()


@router.post(
    "/{workspace_id}/git/fetch", response_model=GitSyncResponse
)
async def git_fetch(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> GitSyncResponse:
    """Fetch refs from origin. No worktree mutation, no merge — purely
    refreshes ahead/behind counts. The cheap version of sync, runs in
    ~1s on small repos."""
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    token = await _read_github_token(
        db, project_id=ws.project_id, actor_id=user.id, vault=vault
    )
    rc, output = await _git_fetch(sandbox, token)
    if rc != 0:
        return GitSyncResponse(
            ok=False, actions=["fetch"], output=output, error=output[-400:]
        )
    ahead, behind = await _ahead_behind(sandbox)
    return GitSyncResponse(
        ok=True, actions=["fetch"], ahead=ahead, behind=behind, output=output
    )


@router.post(
    "/{workspace_id}/git/pull", response_model=GitSyncResponse
)
async def git_pull(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> GitSyncResponse:
    """Fetch + fast-forward merge. Refuses non-fast-forward to keep
    the operator from accidentally creating a merge commit they
    didn't want — they can resolve manually via terminal if needed.

    Sequence: fetch → check fast-forward possible → ff-merge. Skips
    the merge step when already up-to-date (behind=0)."""
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    token = await _read_github_token(
        db, project_id=ws.project_id, actor_id=user.id, vault=vault
    )
    actions: list[str] = []

    rc, fetch_out = await _git_fetch(sandbox, token)
    actions.append("fetch")
    if rc != 0:
        return GitSyncResponse(
            ok=False, actions=actions, output=fetch_out, error=fetch_out[-400:]
        )
    ahead, behind = await _ahead_behind(sandbox)
    if behind == 0:
        return GitSyncResponse(
            ok=True,
            actions=actions,
            ahead=ahead,
            behind=0,
            output=fetch_out + "\n(already up to date)",
        )

    rc, pull_out, pull_err = await _git_exec(
        sandbox, ["pull", "--ff-only", "--no-rebase"], timeout_s=60.0
    )
    actions.append("pull")
    combined = (fetch_out + "\n" + pull_out + pull_err).strip()
    if rc != 0:
        return GitSyncResponse(
            ok=False,
            actions=actions,
            ahead=ahead,
            behind=behind,
            output=combined,
            error=(pull_err or pull_out)[-400:],
        )
    new_ahead, new_behind = await _ahead_behind(sandbox)
    return GitSyncResponse(
        ok=True,
        actions=actions,
        ahead=new_ahead,
        behind=new_behind,
        output=combined,
    )


@router.post(
    "/{workspace_id}/git/sync", response_model=GitSyncResponse
)
async def git_sync(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> GitSyncResponse:
    """VS Code-style "Sync" — combines fetch + pull (ff-only) + push.
    Each sub-step is best-effort: pull failure (non-ff) stops the
    chain, push failure with nothing to push is a no-op success.
    Status flag distinguishes "ahead=0 + behind=0 + nothing pushed"
    (already synced) from real action."""
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    token = await _read_github_token(
        db, project_id=ws.project_id, actor_id=user.id, vault=vault
    )
    actions: list[str] = []
    log_parts: list[str] = []

    rc, out = await _git_fetch(sandbox, token)
    actions.append("fetch")
    log_parts.append(f"$ git fetch\n{out}")
    if rc != 0:
        return GitSyncResponse(
            ok=False, actions=actions, output="\n".join(log_parts), error=out[-400:]
        )

    ahead, behind = await _ahead_behind(sandbox)
    if behind > 0:
        rc, p_out, p_err = await _git_exec(
            sandbox, ["pull", "--ff-only", "--no-rebase"], timeout_s=60.0
        )
        actions.append("pull")
        log_parts.append(f"$ git pull --ff-only\n{(p_out + p_err).strip()}")
        if rc != 0:
            return GitSyncResponse(
                ok=False,
                actions=actions,
                ahead=ahead,
                behind=behind,
                output="\n\n".join(log_parts),
                error=(p_err or p_out)[-400:],
            )
        ahead, behind = await _ahead_behind(sandbox)

    if ahead > 0:
        push_env: dict[str, str] = {}
        push_argv: list[str] = ["push", "origin", "HEAD"]
        if token:
            import base64  # noqa: PLC0415

            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            push_argv = [
                "-c",
                f"http.extraHeader=Authorization: Basic {basic}",
                *push_argv,
            ]
        rc, p_out, p_err = await _git_exec(
            sandbox, push_argv, env=push_env, timeout_s=60.0
        )
        actions.append("push")
        log_parts.append(f"$ git push\n{(p_out + p_err).strip()}")
        if rc != 0:
            return GitSyncResponse(
                ok=False,
                actions=actions,
                ahead=ahead,
                behind=behind,
                output="\n\n".join(log_parts),
                error=(p_err or p_out)[-400:],
            )
        ahead, _ = await _ahead_behind(sandbox)

    return GitSyncResponse(
        ok=True,
        actions=actions,
        ahead=ahead,
        behind=behind,
        output="\n\n".join(log_parts),
    )


async def _ahead_behind(sandbox: WorkspaceSandbox) -> tuple[int, int]:
    """Re-derive ahead/behind via `git status --porcelain=v2 -b` —
    cheap, matches the same logic as `git_status` so the numbers
    don't disagree across endpoints."""
    rc, out, _ = await _git_exec(
        sandbox, ["status", "--porcelain=v2", "-b"], timeout_s=10.0
    )
    if rc != 0:
        return (0, 0)
    for line in out.splitlines():
        if line.startswith("# branch.ab "):
            parts = line[len("# branch.ab ") :].split()
            a, b = 0, 0
            for p in parts:
                try:
                    v = int(p)
                except ValueError:
                    continue
                if p.startswith("+"):
                    a = v
                elif p.startswith("-"):
                    b = abs(v)
            return (a, b)
    return (0, 0)


class GitDiscardRequest(BaseModel):
    """Paths to revert in the worktree. Tracked files: `git restore
    --worktree <paths>` (drops uncommitted edits). Untracked files:
    `rm -- <paths>` (only inside the worktree). Refuses absolute
    paths + parent-dir traversal to keep this from being a generic
    fs-delete primitive."""

    paths: list[str] = Field(min_length=1)


class GitDiscardResponse(BaseModel):
    ok: bool
    discarded: list[str]
    skipped: list[dict[str, str]]


@router.post(
    "/{workspace_id}/git/discard", response_model=GitDiscardResponse
)
async def git_discard(
    workspace_id: str,
    payload: GitDiscardRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitDiscardResponse:
    """Revert unstaged changes per file. For tracked files this is
    `git restore --worktree <path>` (drops working-tree changes,
    leaves any staged copy in the index). For untracked files we
    fall back to `git clean -f -- <path>` so the panel's discard
    button is one-click regardless of whether the row is `M` or `??`.

    Path-safety: each path must be a non-absolute, non-traversing
    relative path. The workspace's `/workspace` is the cwd so any
    `..` segment is rejected outright.
    """
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()

    safe: list[str] = []
    skipped: list[dict[str, str]] = []
    for p in payload.paths:
        if not p or p.startswith("/") or ".." in p.split("/") or _is_gapt_managed_path(p):
            skipped.append({"path": p, "reason": "path rejected by safety filter"})
            continue
        safe.append(p)
    if not safe:
        return GitDiscardResponse(ok=False, discarded=[], skipped=skipped)

    # `git restore --worktree` works for tracked files; for untracked
    # files it errors with "fatal: pathspec ... did not match any
    # file(s) known to git". Run restore first (tracked branch) then
    # `git clean -f` for whatever's left (untracked branch). Two-pass
    # keeps the user from needing to know the distinction.
    rc, restore_out, restore_err = await _git_exec(
        sandbox, ["restore", "--worktree", "--", *safe], timeout_s=15.0
    )
    discarded: list[str] = []
    still_present: list[str] = []
    if rc == 0:
        discarded.extend(safe)
    else:
        # Partial success: parse the failures and try clean -f for
        # those. The error format is one per line: `error: pathspec
        # '<p>' did not match any file(s) known to git`.
        import re as _re  # noqa: PLC0415

        unknown = set(_re.findall(r"pathspec '([^']+)'", restore_err))
        for p in safe:
            if p in unknown:
                still_present.append(p)
            else:
                discarded.append(p)
    if still_present:
        rc, clean_out, clean_err = await _git_exec(
            sandbox, ["clean", "-f", "--", *still_present], timeout_s=15.0
        )
        if rc == 0:
            discarded.extend(still_present)
        else:
            for p in still_present:
                skipped.append({"path": p, "reason": (clean_err or clean_out)[:200]})
    return GitDiscardResponse(
        ok=len(skipped) == 0, discarded=discarded, skipped=skipped
    )


# ─── branches ───────────────────────────────────────────────────────


class GitBranchInfo(BaseModel):
    """One branch row. `current` is the local HEAD; `kind` separates
    local from remote-tracking so the UI can render them in two
    groups without re-parsing the name. `ahead`/`behind` only filled
    for local branches that have an upstream — None on remotes and
    local-without-upstream."""

    name: str
    kind: str  # "local" | "remote"
    current: bool = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    last_commit_sha: str | None = None
    last_commit_subject: str | None = None


class GitBranchesResponse(BaseModel):
    current: str | None
    branches: list[GitBranchInfo]


@router.get(
    "/{workspace_id}/git/branches", response_model=GitBranchesResponse
)
async def git_branches(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitBranchesResponse:
    """List every local + remote-tracking branch with the metadata
    the panel's switcher needs: current flag, upstream tracking pair,
    last commit. Single `git for-each-ref` covers local + remote in
    one shot — way cheaper than calling `git branch` twice."""
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()

    # `for-each-ref` format. Field separator is `\x01` since branch
    # names + commit subjects can both contain pretty much anything
    # else. Locals carry upstream pair; remotes don't.
    fmt = "\x01".join(
        [
            "%(refname:short)",  # 0 name (local: main; remote: origin/main)
            "%(refname)",  # 1 full ref (refs/heads/main, refs/remotes/origin/main)
            "%(HEAD)",  # 2 "*" for current branch, " " otherwise
            "%(upstream:short)",  # 3 upstream (e.g. origin/main) — local only
            "%(upstream:track,nobracket)",  # 4 "ahead 2, behind 1" or empty
            "%(objectname:short)",  # 5 last commit sha
            "%(contents:subject)",  # 6 last commit subject
        ]
    )
    rc, out, err = await _git_exec(
        sandbox,
        [
            "for-each-ref",
            f"--format={fmt}",
            "refs/heads/",
            "refs/remotes/",
        ],
        timeout_s=15.0,
    )
    if rc != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "git.branches_failed", "reason": err.strip()[:400]},
        )

    branches: list[GitBranchInfo] = []
    current: str | None = None
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x01")
        if len(parts) < 7:
            continue
        short, full, head_flag, upstream, track, sha, subject = parts[:7]
        is_local = full.startswith("refs/heads/")
        is_remote = full.startswith("refs/remotes/")
        kind = "local" if is_local else ("remote" if is_remote else "other")
        if kind == "other":
            continue
        # Skip the HEAD ref symlink that points at the default branch
        # — it duplicates the real branch row and would render twice.
        if short.endswith("/HEAD"):
            continue
        is_current = head_flag.strip() == "*"
        if is_current:
            current = short
        ahead: int | None = None
        behind: int | None = None
        if track:
            # `ahead 2, behind 1` / `ahead 2` / `behind 1` / `gone` etc.
            import re as _re  # noqa: PLC0415

            m_a = _re.search(r"ahead (\d+)", track)
            m_b = _re.search(r"behind (\d+)", track)
            ahead = int(m_a.group(1)) if m_a else (0 if upstream else None)
            behind = int(m_b.group(1)) if m_b else (0 if upstream else None)
        branches.append(
            GitBranchInfo(
                name=short,
                kind=kind,
                current=is_current,
                upstream=upstream or None,
                ahead=ahead,
                behind=behind,
                last_commit_sha=sha or None,
                last_commit_subject=subject or None,
            )
        )

    # Sort: current first, then alphabetical within each kind.
    branches.sort(
        key=lambda b: (
            0 if b.kind == "local" else 1,
            0 if b.current else 1,
            b.name.lower(),
        )
    )
    return GitBranchesResponse(current=current, branches=branches)


class GitCheckoutRequest(BaseModel):
    """`branch` is the local branch name to switch to. With
    `create=True`, creates it from the current HEAD (`git checkout -b`).
    `start_point` lets the UI branch from a remote: e.g. create
    `feat-x` starting at `origin/main`. Refuses by default when there
    are uncommitted worktree changes — set `force=True` to override
    (calls `git checkout -f`, lose-on-purpose semantics)."""

    branch: str = Field(min_length=1, max_length=200)
    create: bool = False
    start_point: str | None = None
    force: bool = False


class GitCheckoutResponse(BaseModel):
    ok: bool
    branch: str
    output: str
    error: str | None = None


@router.post(
    "/{workspace_id}/git/checkout", response_model=GitCheckoutResponse
)
async def git_checkout(
    workspace_id: str,
    payload: GitCheckoutRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitCheckoutResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    argv: list[str] = ["checkout"]
    if payload.force:
        argv.append("-f")
    if payload.create:
        argv.append("-b")
    argv.append(payload.branch)
    if payload.start_point:
        argv.append(payload.start_point)
    rc, out, err = await _git_exec(sandbox, argv, timeout_s=30.0)
    combined = (out + err).strip()
    return GitCheckoutResponse(
        ok=rc == 0,
        branch=payload.branch,
        output=combined,
        error=err.strip()[-400:] if rc != 0 else None,
    )


class GitBranchDeleteRequest(BaseModel):
    branch: str = Field(min_length=1, max_length=200)
    # By default we refuse to delete an unmerged branch (`git branch
    # -d`); `force=True` flips to `-D` so the operator can drop a
    # merged-by-eye branch the tracking machinery doesn't recognise.
    force: bool = False


class GitBranchDeleteResponse(BaseModel):
    ok: bool
    branch: str
    output: str
    error: str | None = None


@router.post(
    "/{workspace_id}/git/branch/delete",
    response_model=GitBranchDeleteResponse,
)
async def git_branch_delete(
    workspace_id: str,
    payload: GitBranchDeleteRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitBranchDeleteResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    flag = "-D" if payload.force else "-d"
    rc, out, err = await _git_exec(
        sandbox, ["branch", flag, payload.branch], timeout_s=10.0
    )
    return GitBranchDeleteResponse(
        ok=rc == 0,
        branch=payload.branch,
        output=(out + err).strip(),
        error=err.strip()[-400:] if rc != 0 else None,
    )


# ─── stash ──────────────────────────────────────────────────────────


class GitStashEntry(BaseModel):
    """`ref` is `stash@{N}` — that's the form `git stash pop/drop`
    wants. `subject` is the auto-generated or user-supplied message."""

    ref: str
    branch: str | None
    subject: str
    age_seconds: int | None = None


class GitStashListResponse(BaseModel):
    entries: list[GitStashEntry]


@router.get(
    "/{workspace_id}/git/stash/list", response_model=GitStashListResponse
)
async def git_stash_list(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitStashListResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    # `git stash list --format=...`. We grab the ref name, branch
    # name (parsed from subject by git), the subject after the colon,
    # and the relative age. Use `\x01` separator for same reason
    # as branches above.
    fmt = "\x01".join(["%gd", "%cr", "%s"])  # gd = stash ref, cr = committer rel, s = subject
    rc, out, _ = await _git_exec(
        sandbox, ["stash", "list", f"--format={fmt}"], timeout_s=10.0
    )
    entries: list[GitStashEntry] = []
    if rc == 0:
        import re as _re  # noqa: PLC0415

        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\x01")
            if len(parts) < 3:
                continue
            ref, _age_rel, subject = parts[:3]
            # Subject format: `WIP on <branch>: <sha> <msg>` or `On <branch>: <msg>`
            branch: str | None = None
            m = _re.match(r"(?:WIP on|On) ([^:]+):", subject)
            if m:
                branch = m.group(1).strip()
            entries.append(
                GitStashEntry(
                    ref=ref.strip(),
                    branch=branch,
                    subject=subject.strip(),
                )
            )
    return GitStashListResponse(entries=entries)


class GitStashPushRequest(BaseModel):
    message: str | None = None
    include_untracked: bool = False


class GitStashOpResponse(BaseModel):
    ok: bool
    output: str
    error: str | None = None


@router.post(
    "/{workspace_id}/git/stash/push", response_model=GitStashOpResponse
)
async def git_stash_push(
    workspace_id: str,
    payload: GitStashPushRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitStashOpResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    argv: list[str] = ["stash", "push"]
    if payload.include_untracked:
        argv.append("-u")
    if payload.message:
        argv.extend(["-m", payload.message])
    rc, out, err = await _git_exec(sandbox, argv, timeout_s=20.0)
    return GitStashOpResponse(
        ok=rc == 0,
        output=(out + err).strip(),
        error=err.strip()[-400:] if rc != 0 else None,
    )


class GitStashRefRequest(BaseModel):
    """Defaults to `stash@{0}` (most recent) when `ref` is omitted —
    matches `git stash pop` / `git stash drop` semantics."""

    ref: str | None = None


@router.post(
    "/{workspace_id}/git/stash/pop", response_model=GitStashOpResponse
)
async def git_stash_pop(
    workspace_id: str,
    payload: GitStashRefRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitStashOpResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    argv: list[str] = ["stash", "pop"]
    if payload.ref:
        argv.append(payload.ref)
    rc, out, err = await _git_exec(sandbox, argv, timeout_s=20.0)
    return GitStashOpResponse(
        ok=rc == 0,
        output=(out + err).strip(),
        error=err.strip()[-400:] if rc != 0 else None,
    )


@router.post(
    "/{workspace_id}/git/stash/drop", response_model=GitStashOpResponse
)
async def git_stash_drop(
    workspace_id: str,
    payload: GitStashRefRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitStashOpResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    argv: list[str] = ["stash", "drop"]
    if payload.ref:
        argv.append(payload.ref)
    rc, out, err = await _git_exec(sandbox, argv, timeout_s=10.0)
    return GitStashOpResponse(
        ok=rc == 0,
        output=(out + err).strip(),
        error=err.strip()[-400:] if rc != 0 else None,
    )


# ─── commit log (for graph view) ────────────────────────────────────


class GitLogCommit(BaseModel):
    """One commit row from `git log`. `parents` is the list of parent
    SHAs (length >1 = merge commit) and lets the frontend draw a
    simple ASCII graph. `refs` is the decorate string split into the
    branch/tag tags that point at this commit."""

    sha: str
    short_sha: str
    parents: list[str]
    author: str
    author_email: str
    iso_date: str
    subject: str
    refs: list[str]


class GitLogResponse(BaseModel):
    commits: list[GitLogCommit]


@router.get(
    "/{workspace_id}/git/log", response_model=GitLogResponse
)
async def git_log(
    workspace_id: str,
    limit: int = 50,
    all_branches: bool = True,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> GitLogResponse:
    """Recent commit history with parent SHAs so the panel can draw
    a graph. `all_branches=True` follows `--all` for VS Code-style
    "show every branch" view; flip to False for "current branch only"
    if the panel adds a filter toggle later."""
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)
    await sandbox.ensure()
    limit = max(1, min(limit, 500))
    fmt = "\x01".join(
        [
            "%H",  # full SHA
            "%h",  # short SHA
            "%P",  # parent SHAs (space-separated)
            "%an",  # author name
            "%ae",  # author email
            "%aI",  # author date ISO 8601
            "%s",  # subject
            "%D",  # ref names (without parens)
        ]
    )
    argv = ["log", f"--max-count={limit}", f"--format={fmt}"]
    if all_branches:
        argv.append("--all")
    rc, out, err = await _git_exec(sandbox, argv, timeout_s=15.0)
    if rc != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "git.log_failed", "reason": err.strip()[:400]},
        )
    commits: list[GitLogCommit] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x01")
        if len(parts) < 8:
            continue
        sha, short, parents, author, email, date, subject, refs = parts[:8]
        commits.append(
            GitLogCommit(
                sha=sha.strip(),
                short_sha=short.strip(),
                parents=[p for p in parents.split() if p],
                author=author.strip(),
                author_email=email.strip(),
                iso_date=date.strip(),
                subject=subject.strip(),
                refs=[r.strip() for r in refs.split(",") if r.strip()],
            )
        )
    return GitLogResponse(commits=commits)


# Anchor for static analysers — the BaseModel imports below are
# pulled in by pydantic at runtime via field annotations.
_USED: tuple[type, ...] = (BaseModel, shlex.__class__)
