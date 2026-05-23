"""CI surface — `GET /api/projects/{pid}/ci/runs`.

Wraps `GithubProvider.list_workflow_runs` so the UI can show recent
runs + their status. `repo` is parsed from the project's
`git_remote_url`; the token comes from settings
(`GAPT_CI_GITHUB_TOKEN`) until per-project Secret Vault lookup
ships.

Auth model in M1: server-wide dev token via `GAPT_CI_GITHUB_TOKEN`.
Production wires the project's `git_auth_secret_ref` through
SecretVault — follow-up cycle once we settle the per-actor vs
server-shared token question.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from gapt_server.container import get_app_settings, get_db_session
from gapt_server.db import models  # noqa: TC001 — pydantic + Depends runtime introspection
from gapt_server.domains.git import GithubProvider, GitOperationError
from gapt_server.domains.git.provider import WorkflowRunStatus  # noqa: TC001 — pydantic field type
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

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


@router.get(
    "/{project_id}/ci/runs",
    response_model=list[CiRunResponse],
)
async def list_ci_runs(
    project_id: str,
    branch: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> list[CiRunResponse]:
    try:
        project = await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    if not settings.ci_github_token:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "ci.no_token",
                "reason": (
                    "no GitHub token configured for the CI surface — set "
                    "GAPT_CI_GITHUB_TOKEN or attach a project secret"
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

    provider = _build_provider(settings.ci_github_token, repo)
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
