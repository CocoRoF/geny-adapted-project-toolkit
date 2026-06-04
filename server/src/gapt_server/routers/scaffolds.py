"""Phase N — scaffold preset listing + project-from-scaffold endpoint."""

from __future__ import annotations

import re
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gapt_server.container import (
    AppContainer,
    get_audit_sink,
    get_container,
    get_db_session,
)
from gapt_server.db import enums
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
from gapt_server.domains.auth import AdminPrincipal  # noqa: TC001
from gapt_server.domains.projects.service import (
    ProjectError,
    ProjectService,
)
from gapt_server.domains.scaffolds.context import RenderContext
from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode
from gapt_server.domains.scaffolds.github_client import GithubClient
from gapt_server.domains.scaffolds.pusher import push_scaffold
from gapt_server.domains.scaffolds.registry import all_presets, get_preset
from gapt_server.domains.scaffolds.token_resolver import resolve_github_token
from gapt_server.domains.secrets.vault import SecretVault  # noqa: TC001
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import (
    ProjectResponse,
    http_from_project_error,
)
from gapt_server.routers.secrets import get_vault

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/_gapt/api/scaffolds", tags=["scaffolds"])
projects_router = APIRouter(
    prefix="/_gapt/api/projects", tags=["scaffolds"]
)

_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
# GitHub's documented repo name rules: alphanumerics + hyphen + underscore
# + dot, no leading dot, ≤ 100 chars. We're a touch stricter — leading
# hyphen and trailing dot trip git itself in some clients.
_REPO_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$"


# ─────────────────────────────────────────────────── listing ──


@router.get("", response_model=dict[str, Any])
async def list_scaffolds(
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Return every registered preset's summary shape."""
    del user
    return {"presets": [p.to_summary_dict() for p in all_presets()]}


# ────────────────────────────────────────────────── create ──


class ScaffoldRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120, pattern=_SLUG_PATTERN)
    display_name: str = Field(min_length=1, max_length=200)
    repo_name: str = Field(min_length=1, max_length=100, pattern=_REPO_NAME_PATTERN)
    repo_visibility: Literal["private", "public"] = "private"
    preset_id: str = Field(min_length=1, max_length=64)
    preset_options: dict[str, Any] = Field(default_factory=dict)


class ScaffoldRepoInfo(BaseModel):
    name: str
    full_name: str
    html_url: str
    clone_url: str
    default_branch: str
    private: bool


class ScaffoldSummary(BaseModel):
    files_created: int
    commit_sha: str
    preset_id: str


class ScaffoldResponse(BaseModel):
    project: ProjectResponse
    repo: ScaffoldRepoInfo
    scaffold_summary: ScaffoldSummary


def _http_from_scaffold_error(exc: ScaffoldError) -> HTTPException:
    """Map our domain error codes to HTTP status codes."""
    if exc.code in {
        ScaffoldErrorCode.TOKEN_MISSING,
        ScaffoldErrorCode.TOKEN_SCOPE_INSUFFICIENT,
        ScaffoldErrorCode.TOKEN_INVALID,
    }:
        return HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": exc.code.value, "reason": exc.reason},
        )
    if exc.code is ScaffoldErrorCode.REPO_EXISTS:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code.value, "reason": exc.reason},
        )
    if exc.code in {
        ScaffoldErrorCode.PRESET_UNKNOWN,
        ScaffoldErrorCode.OPTION_INVALID,
    }:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code.value, "reason": exc.reason},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": exc.code.value, "reason": exc.reason},
    )


_REQUIRED_REPO_SCOPES = ({"repo"}, {"public_repo"})


def _scope_ok(scopes: set[str]) -> bool:
    """Token must include AT LEAST one of these scope bundles."""
    return any(required.issubset(scopes) for required in _REQUIRED_REPO_SCOPES)


@projects_router.post(
    "/scaffold",
    response_model=ScaffoldResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_from_scaffold(  # noqa: PLR0915 — single-transaction flow; splitting would obscure rollback ordering
    payload: ScaffoldRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ScaffoldResponse:
    """Create a new GitHub repo + push a preset scaffold + record a
    GAPT project row in one transaction.

    Failure modes:
      * 412 — token missing / lacks `repo` scope / rejected by GitHub
      * 409 — preset / GAPT slug / GitHub repo name collision
      * 422 — preset id unknown OR options didn't match schema
      * 500 — GitHub API / git push subprocess failed (rollback attempted)
    """
    try:
        preset = get_preset(payload.preset_id)
    except ScaffoldError as exc:
        raise _http_from_scaffold_error(exc) from exc

    try:
        cleaned_options = preset.validate_options(payload.preset_options)
    except ScaffoldError as exc:
        raise _http_from_scaffold_error(exc) from exc

    # 1. Token + scope check.
    try:
        token = await resolve_github_token(
            db=db,
            vault=vault,
            actor_id=user.id,
            fallback=container.host_github_token,
            purpose="scaffold.create_repo",
        )
    except ScaffoldError as exc:
        raise _http_from_scaffold_error(exc) from exc

    async with GithubClient(token) as gh:
        try:
            scopes = await gh.get_scopes()
        except ScaffoldError as exc:
            raise _http_from_scaffold_error(exc) from exc

        if scopes and not _scope_ok(scopes):
            raise _http_from_scaffold_error(
                ScaffoldError(
                    ScaffoldErrorCode.TOKEN_SCOPE_INSUFFICIENT,
                    f"GitHub token needs `repo` (or `public_repo`) scope; has {sorted(scopes)}",
                )
            )
        if not scopes:
            # fine-grained PAT — we don't get the header.
            raise _http_from_scaffold_error(
                ScaffoldError(
                    ScaffoldErrorCode.TOKEN_SCOPE_INSUFFICIENT,
                    (
                        "Fine-grained PATs aren't supported yet. Use a classic "
                        "Personal Access Token with the `repo` scope checked."
                    ),
                )
            )

        # 2. Owner.
        try:
            user_info = await gh.get_user()
        except ScaffoldError as exc:
            raise _http_from_scaffold_error(exc) from exc
        owner_login = str(user_info.get("login") or "")
        if not owner_login:
            raise _http_from_scaffold_error(
                ScaffoldError(
                    ScaffoldErrorCode.USER_FETCH_FAILED,
                    "GitHub `/user` response had no `login` field",
                )
            )

        # 3. Pre-check repo doesn't already exist (better UX than the
        #    422 we'd get from create_repo).
        try:
            if await gh.repo_exists(owner_login, payload.repo_name):
                raise _http_from_scaffold_error(
                    ScaffoldError(
                        ScaffoldErrorCode.REPO_EXISTS,
                        f"repo {owner_login}/{payload.repo_name} already exists on GitHub",
                    )
                )
        except ScaffoldError as exc:
            raise _http_from_scaffold_error(exc) from exc

        # 4. Render the scaffold tree.
        ctx = RenderContext(
            project_name=payload.display_name,
            slug=payload.slug,
            repo_name=payload.repo_name,
            github_owner=owner_login,
            options=cleaned_options,
        )
        try:
            files = preset.render(ctx)
        except Exception as exc:  # noqa: BLE001 — surface anything as RENDER_FAILED
            raise _http_from_scaffold_error(
                ScaffoldError(
                    ScaffoldErrorCode.RENDER_FAILED,
                    f"preset {preset.id!r} render crashed: {exc!s}",
                )
            ) from exc

        # 5. Create the GitHub repo.
        try:
            repo_info = await gh.create_repo(
                name=payload.repo_name,
                private=payload.repo_visibility == "private",
                description=f"{payload.display_name} (created by GAPT scaffold)",
            )
        except ScaffoldError as exc:
            raise _http_from_scaffold_error(exc) from exc

        # 6. Push the scaffold. Roll back the repo on push failure.
        try:
            commit_sha = await push_scaffold(
                clone_url=repo_info.clone_url,
                token=token,
                files=files,
                branch=repo_info.default_branch,
                commit_message=f"Initial scaffold ({preset.id})",
            )
        except ScaffoldError as exc:
            logger.warning(
                "scaffold.push_failed_rolling_back",
                owner=owner_login,
                repo=repo_info.name,
                reason=exc.reason,
            )
            await gh.delete_repo(owner_login, repo_info.name)
            raise _http_from_scaffold_error(exc) from exc

    # 7. Record the GAPT project row.
    default_compose_paths: list[str] = []
    compose_path = preset.deploy_target_defaults.get("compose_path")
    if isinstance(compose_path, str) and compose_path:
        default_compose_paths.append(compose_path)

    service = ProjectService(audit_sink=audit_sink)
    try:
        project_view = await service.create(
            db,
            actor=user,
            slug=payload.slug,
            display_name=payload.display_name,
            git_remote_url=repo_info.html_url,
            git_provider=enums.GitProvider.GITHUB,
            default_compose_paths=default_compose_paths,
            scaffold_preset_id=preset.id,
        )
        await db.commit()
    except ProjectError as exc:
        await db.rollback()
        # The repo + scaffold push succeeded; we DON'T delete the
        # GitHub repo here because the operator can still re-import
        # via the "Import" flow with a different slug if the only
        # problem was a slug collision.
        raise http_from_project_error(exc) from exc

    logger.info(
        "scaffold.create_done",
        owner=owner_login,
        repo=repo_info.name,
        slug=payload.slug,
        preset=preset.id,
        commit_sha=commit_sha,
        files_pushed=len(files),
    )

    return ScaffoldResponse(
        project=ProjectResponse.from_view(project_view),
        repo=ScaffoldRepoInfo(
            name=repo_info.name,
            full_name=repo_info.full_name,
            html_url=repo_info.html_url,
            clone_url=repo_info.clone_url,
            default_branch=repo_info.default_branch,
            private=repo_info.private,
        ),
        scaffold_summary=ScaffoldSummary(
            files_created=len(files),
            commit_sha=commit_sha,
            preset_id=preset.id,
        ),
    )
