"""CI surface — `GET /api/projects/{pid}/ci/runs`.

Wraps `GithubProvider.list_workflow_runs` so the UI can show recent
runs + their status. `repo` is parsed from the project's
`git_remote_url`; the token resolution order is:

  1. The acting user's `github_token` stored in Settings (Vault, USER
     scope, key_name="github_token")
  2. The server-wide `GAPT_CI_GITHUB_TOKEN` env (dev convenience)

Per-project / per-org tokens land in M2 when the SecretRefMap UI
ships — for M1.5 we prefer user-scoped because that's the single
field the user is asked to fill out in Settings.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from gapt_server.container import get_app_settings, get_db_session
from gapt_server.db import enums
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.git import GithubProvider, GitOperationError
from gapt_server.domains.git.provider import WorkflowRunStatus  # noqa: TC001 — pydantic field type
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.domains.secrets.vault import SecretVault, SecretVaultError
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error
from gapt_server.routers.secrets import get_vault

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


router = APIRouter(prefix="/api/projects", tags=["ci"])


class CiRunResponse(BaseModel):
    id: int
    name: str
    head_branch: str
    head_sha: str
    status: WorkflowRunStatus
    html_url: str


# Accept `https://github.com/owner/repo[.git]`, `git@github.com:owner/repo[.git]`,
# or `owner/repo`. The github.com-anchored pattern wins; the bare
# `owner/repo` form is a fallback for dogfood / dev.
_GH_URL_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")
_BARE_RE = re.compile(r"^(?P<owner>[A-Za-z0-9._-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?$")


def parse_github_repo(remote_url: str) -> str | None:
    text = remote_url.strip()
    match = _GH_URL_RE.search(text)
    if match is not None:
        return f"{match['owner']}/{match['name']}"
    bare = _BARE_RE.match(text)
    if bare is not None:
        return f"{bare['owner']}/{bare['name']}"
    return None


def _build_provider(token: str, repo: str) -> GithubProvider:
    return GithubProvider(token=token, repo=repo)


async def _resolve_github_token(
    *,
    db: AsyncSession,
    vault: SecretVault,
    actor_id: str,
    fallback: str | None,
) -> str | None:
    """Admin-scoped vault first, then `GAPT_CI_GITHUB_TOKEN` fallback."""
    try:
        metadata = await vault.list(
            db, scope=enums.SecretOwnerScope.SYSTEM, owner_id=actor_id
        )
    except SecretVaultError:
        return fallback
    for md in metadata:
        if md.key_name != "github_token":
            continue
        try:
            return await vault.read(
                db, secret_id=md.id, purpose="ci.list_runs", actor_id=actor_id
            )
        except SecretVaultError:
            break
    return fallback


@router.get(
    "/{project_id}/ci/runs",
    response_model=list[CiRunResponse],
)
async def list_ci_runs(
    project_id: str,
    branch: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> list[CiRunResponse]:
    try:
        project = await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    token = await _resolve_github_token(
        db=db,
        vault=vault,
        actor_id=user.id,
        fallback=settings.ci_github_token,
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "ci.no_token",
                "reason": (
                    "no GitHub token configured for the CI surface — save one in "
                    "Settings → Credentials → GitHub Personal Access Token, or "
                    "set GAPT_CI_GITHUB_TOKEN on the server"
                ),
            },
        )

    repo = parse_github_repo(project.git_remote_url)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "ci.repo_unparseable",
                "reason": (
                    f"could not extract owner/repo from {project.git_remote_url!r} "
                    "— only GitHub remotes are supported by this endpoint today"
                ),
            },
        )

    provider = _build_provider(token, repo)
    try:
        runs = await provider.list_workflow_runs(branch=branch, limit=limit)
    except GitOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc

    return [
        CiRunResponse(
            id=r.id,
            name=r.name,
            head_branch=r.head_branch,
            head_sha=r.head_sha,
            status=r.status,
            html_url=r.html_url,
        )
        for r in runs
    ]


class CiLogResponse(BaseModel):
    run_id: int
    log: str
    # Soft cap so a 10 MB CI log doesn't tank the SPA. The full log is
    # always available via the GitHub Actions UI (html_url on the run).
    truncated: bool = False


_LOG_CAP_BYTES = 256 * 1024


async def _provider_for_project(
    project_id: str,
    *,
    db: AsyncSession,
    user: AdminPrincipal,
    settings: Settings,
    vault: SecretVault,
):  # type: ignore[no-untyped-def]
    """Shared lookup helper: resolve project + token + repo +
    GithubProvider. Raises the same HTTP errors as `list_ci_runs` for
    consistency."""
    try:
        project = await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    token = await _resolve_github_token(
        db=db, vault=vault, actor_id=user.id, fallback=settings.ci_github_token
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "ci.no_token",
                "reason": "save a GitHub PAT in Settings → Credentials, or set GAPT_CI_GITHUB_TOKEN",
            },
        )
    repo = parse_github_repo(project.git_remote_url)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "ci.repo_unparseable",
                "reason": f"could not extract owner/repo from {project.git_remote_url!r}",
            },
        )
    return _build_provider(token, repo)


@router.get(
    "/{project_id}/ci/runs/{run_id}/logs",
    response_model=CiLogResponse,
)
async def get_ci_run_logs(
    project_id: str,
    run_id: int,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> CiLogResponse:
    """Fetch the (possibly large) CI run log via `gh run view --log`.
    Capped at 256 KB; full log lives on the GitHub Actions UI."""
    provider = await _provider_for_project(
        project_id, db=db, user=user, settings=settings, vault=vault
    )
    try:
        full = await provider.get_workflow_run_logs(run_id=run_id)
    except GitOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    if len(full.encode("utf-8")) > _LOG_CAP_BYTES:
        encoded = full.encode("utf-8")[:_LOG_CAP_BYTES]
        last_nl = encoded.rfind(b"\n")
        if last_nl > 0:
            encoded = encoded[:last_nl]
        return CiLogResponse(
            run_id=run_id,
            log=encoded.decode("utf-8", errors="replace")
            + "\n\n[…truncated — open the run on GitHub for the full log…]\n",
            truncated=True,
        )
    return CiLogResponse(run_id=run_id, log=full, truncated=False)


class RerunResponse(BaseModel):
    run_id: int
    failed_only: bool


@router.post(
    "/{project_id}/ci/runs/{run_id}/rerun",
    response_model=RerunResponse,
)
async def rerun_ci_run(
    project_id: str,
    run_id: int,
    failed_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
) -> RerunResponse:
    """Re-run an entire workflow run, or just its failed jobs.
    Requires the user's PAT to carry the `workflow` scope."""
    provider = await _provider_for_project(
        project_id, db=db, user=user, settings=settings, vault=vault
    )
    try:
        await provider.rerun_workflow_run(run_id=run_id, failed_only=failed_only)
    except GitOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    return RerunResponse(run_id=run_id, failed_only=failed_only)
