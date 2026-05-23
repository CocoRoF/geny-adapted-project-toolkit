"""Project service — CRUD + Environment CRUD + ownership checks.

The actor calling this service must already be authenticated (the
router takes care of that). Authorisation is verified inside each
method: a project membership row needs to exist with at least the
required role, or the caller must be project / org owner.

Clone / checkout / sandbox boot stay out of scope for M1-E1 — they
land in M1-E2 (the agent + git cycle).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditAction, AuditEvent, AuditSink, NullAuditSink

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ProjectError(RuntimeError):
    """Domain error — carries a stable code suffix for the API layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProjectView:
    id: str
    org_id: str
    owner_id: str
    slug: str
    display_name: str
    git_remote_url: str
    git_provider: enums.GitProvider
    default_compose_paths: list[str]
    compose_profile_dev: str | None
    compose_profile_prod: str | None
    created_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True)
class EnvironmentView:
    id: str
    project_id: str
    name: str
    deploy_target_kind: enums.DeployTargetKind
    deploy_target_config: dict[str, Any]
    require_2fa: bool
    secret_refs: list[str]
    cost_multiplier: float
    hooks: dict[str, Any]
    created_at: datetime


def _project_view(row: models.Project) -> ProjectView:
    return ProjectView(
        id=row.id,
        org_id=row.org_id,
        owner_id=row.owner_id,
        slug=row.slug,
        display_name=row.display_name,
        git_remote_url=row.git_remote_url,
        git_provider=row.git_provider,
        default_compose_paths=list(row.default_compose_paths or []),
        compose_profile_dev=row.compose_profile_dev,
        compose_profile_prod=row.compose_profile_prod,
        created_at=row.created_at,
        archived_at=row.archived_at,
    )


def _env_view(row: models.Environment) -> EnvironmentView:
    return EnvironmentView(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        deploy_target_kind=row.deploy_target_kind,
        deploy_target_config=dict(row.deploy_target_config or {}),
        require_2fa=row.require_2fa,
        secret_refs=list(row.secret_refs or []),
        cost_multiplier=float(row.cost_multiplier),
        hooks=dict(row.hooks or {}),
        created_at=row.created_at,
    )


class ProjectService:
    """Encapsulates project + environment CRUD with audit + auth checks."""

    def __init__(self, audit_sink: AuditSink | None = None) -> None:
        self._audit: AuditSink = audit_sink or NullAuditSink()

    # ───────────────────────────────────────────────── projects ──

    async def create(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        org_id: str,
        slug: str,
        display_name: str,
        git_remote_url: str,
        git_provider: enums.GitProvider,
        default_compose_paths: list[str] | None = None,
        compose_profile_dev: str | None = None,
        compose_profile_prod: str | None = None,
        git_auth_secret_ref: str | None = None,
    ) -> ProjectView:
        await _ensure_org_member(db, actor=actor, org_id=org_id)

        project = models.Project(
            id=new_ulid(),
            slug=slug,
            org_id=org_id,
            owner_id=actor.id,
            display_name=display_name,
            git_remote_url=git_remote_url,
            git_provider=git_provider,
            git_auth_secret_ref=git_auth_secret_ref,
            default_compose_paths=default_compose_paths or [],
            compose_profile_dev=compose_profile_dev,
            compose_profile_prod=compose_profile_prod,
        )
        db.add(project)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise ProjectError(
                "project.slug_taken",
                f"slug={slug!r} already exists in org={org_id}",
            ) from exc

        # Creator gets a project-level OWNER membership automatically.
        db.add(
            models.ProjectMembership(
                project_id=project.id,
                user_id=actor.id,
                role=enums.Role.OWNER,
            )
        )
        await db.flush()

        view = _project_view(project)
        await self._audit.log(
            AuditEvent(
                action=AuditAction.PROJECT_CREATE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"org_id": org_id, "project_id": project.id},
                subject={"slug": slug, "display_name": display_name},
                payload={
                    "git_remote_url": git_remote_url,
                    "git_provider": git_provider.value,
                },
            )
        )
        return view

    async def list_for_user(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        org_id: str | None = None,
        include_archived: bool = False,
    ) -> list[ProjectView]:
        stmt = (
            select(models.Project)
            .join(
                models.ProjectMembership,
                models.ProjectMembership.project_id == models.Project.id,
            )
            .where(models.ProjectMembership.user_id == actor.id)
            .order_by(models.Project.created_at.desc())
        )
        if org_id is not None:
            stmt = stmt.where(models.Project.org_id == org_id)
        if not include_archived:
            stmt = stmt.where(models.Project.archived_at.is_(None))
        rows = (await db.execute(stmt)).scalars().all()
        return [_project_view(r) for r in rows]

    async def get(self, db: AsyncSession, *, actor: models.User, project_id: str) -> ProjectView:
        row = await fetch_project_for(db, actor=actor, project_id=project_id)
        return _project_view(row)

    async def update(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        project_id: str,
        display_name: str | None = None,
        default_compose_paths: list[str] | None = None,
        compose_profile_dev: str | None = None,
        compose_profile_prod: str | None = None,
    ) -> ProjectView:
        row = await fetch_project_for(
            db, actor=actor, project_id=project_id, min_role=enums.Role.EDITOR
        )
        if display_name is not None:
            row.display_name = display_name
        if default_compose_paths is not None:
            row.default_compose_paths = default_compose_paths
        if compose_profile_dev is not None:
            row.compose_profile_dev = compose_profile_dev
        if compose_profile_prod is not None:
            row.compose_profile_prod = compose_profile_prod
        await db.flush()
        view = _project_view(row)
        await self._audit.log(
            AuditEvent(
                action=AuditAction.PROJECT_UPDATE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": project_id},
                subject={"display_name": row.display_name},
            )
        )
        return view

    async def archive(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        project_id: str,
    ) -> ProjectView:
        row = await fetch_project_for(
            db, actor=actor, project_id=project_id, min_role=enums.Role.ADMIN
        )
        row.archived_at = datetime.now(tz=UTC)
        await db.flush()
        view = _project_view(row)
        await self._audit.log(
            AuditEvent(
                action=AuditAction.PROJECT_ARCHIVE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": project_id},
            )
        )
        return view

    # ─────────────────────────────────────────────── environments ──

    async def create_environment(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        project_id: str,
        name: str,
        deploy_target_kind: enums.DeployTargetKind,
        deploy_target_config: dict[str, Any] | None = None,
        require_2fa: bool = False,
        secret_refs: list[str] | None = None,
        cost_multiplier: float = 1.0,
        hooks: dict[str, Any] | None = None,
    ) -> EnvironmentView:
        await fetch_project_for(db, actor=actor, project_id=project_id, min_role=enums.Role.ADMIN)
        env = models.Environment(
            id=new_ulid(),
            project_id=project_id,
            name=name,
            deploy_target_kind=deploy_target_kind,
            deploy_target_config=deploy_target_config or {},
            require_2fa=require_2fa,
            secret_refs=secret_refs or [],
            cost_multiplier=cost_multiplier,
            hooks=hooks or {},
        )
        db.add(env)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise ProjectError(
                "environment.name_taken",
                f"name={name!r} already exists in project={project_id}",
            ) from exc
        return _env_view(env)

    async def list_environments(
        self, db: AsyncSession, *, actor: models.User, project_id: str
    ) -> list[EnvironmentView]:
        await fetch_project_for(db, actor=actor, project_id=project_id)
        rows = (
            (
                await db.execute(
                    select(models.Environment)
                    .where(models.Environment.project_id == project_id)
                    .order_by(models.Environment.name)
                )
            )
            .scalars()
            .all()
        )
        return [_env_view(r) for r in rows]


# ─────────────────────────────────────────────────────────── helpers ──


_ROLE_ORDER: dict[enums.Role, int] = {
    enums.Role.VIEWER: 0,
    enums.Role.EDITOR: 1,
    enums.Role.ADMIN: 2,
    enums.Role.OWNER: 3,
}


async def _ensure_org_member(db: AsyncSession, *, actor: models.User, org_id: str) -> None:
    row = (
        await db.execute(
            select(models.OrgMembership).where(
                (models.OrgMembership.org_id == org_id) & (models.OrgMembership.user_id == actor.id)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProjectError(
            "org.forbidden",
            f"user {actor.id} is not a member of org {org_id}",
        )


async def fetch_project_for(
    db: AsyncSession,
    *,
    actor: models.User,
    project_id: str,
    min_role: enums.Role = enums.Role.VIEWER,
) -> models.Project:
    project = (
        await db.execute(select(models.Project).where(models.Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise ProjectError("project.not_found", f"project_id={project_id}")

    membership = (
        await db.execute(
            select(models.ProjectMembership).where(
                (models.ProjectMembership.project_id == project_id)
                & (models.ProjectMembership.user_id == actor.id)
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise ProjectError(
            "project.forbidden",
            f"user {actor.id} has no membership on project {project_id}",
        )
    if _ROLE_ORDER[membership.role] < _ROLE_ORDER[min_role]:
        raise ProjectError(
            "project.role_insufficient",
            f"user {actor.id} role={membership.role.value} < required {min_role.value}",
        )
    return project
