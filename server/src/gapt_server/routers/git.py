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


# Anchor for static analysers — the BaseModel imports below are
# pulled in by pydantic at runtime via field annotations.
_USED: tuple[type, ...] = (BaseModel, shlex.__class__)
