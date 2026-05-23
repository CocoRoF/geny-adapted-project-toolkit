"""Freshness policy — idle / stale / archived state machine for sessions.

Plan §2.8 specifies four bands based on time since
``agent_sessions.last_active_at``:

| Elapsed                | Action          | DB status         |
|------------------------|-----------------|-------------------|
| < 5 min                | KEEP_ACTIVE     | ACTIVE            |
| 5 min  ≤ t < 30 min    | (no transition) | ACTIVE            |
| 30 min ≤ t < 6 hours   | NOTIFY_IDLE     | STALE_IDLE        |
| 6 hours ≤ t < 24 hours | PAUSE_SANDBOX   | STALE_COMPACT     |
| ≥ 24 hours             | ARCHIVE         | ARCHIVED          |

The Cycle 2.8b PR ships the policy + the in-memory runner; the ARQ
worker wire-up (a scheduled tick) lands once we adopt Redis (M2).
Until then ``FreshnessRunner.run_once`` is callable from any
async loop (tests, dev CLI, a future scheduler).

Sandbox pause / archive *side effects* (SandboxBackend.stop) plug in
via the optional ``on_pause`` / ``on_archive`` callbacks — keeping the
core dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from gapt_server.db import enums, models
from gapt_server.domains.audit.sink import AuditEvent, AuditSink, NullAuditSink

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class FreshnessAction(StrEnum):
    KEEP_ACTIVE = "keep_active"
    NOTIFY_IDLE = "notify_idle"
    PAUSE_SANDBOX = "pause_sandbox"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class FreshnessThresholds:
    """Configurable seconds-since-last-activity bands."""

    idle_notice_s: float = 30 * 60  # 30 min
    pause_s: float = 6 * 60 * 60  # 6 h
    archive_s: float = 24 * 60 * 60  # 24 h


@dataclass(frozen=True)
class FreshnessPolicy:
    thresholds: FreshnessThresholds = field(default_factory=FreshnessThresholds)

    def evaluate(  # noqa: PLR0911  — 4-band ladder reads cleaner as straight returns
        self,
        *,
        last_active_at: datetime,
        current_status: enums.AgentSessionStatus,
        now: datetime,
    ) -> FreshnessAction:
        """Decide the *transition* the runner should apply. Idempotent:
        returns the same answer for the same input + a session whose
        status already matches stays put."""
        if current_status == enums.AgentSessionStatus.ARCHIVED:
            return FreshnessAction.KEEP_ACTIVE

        elapsed = (now - last_active_at).total_seconds()
        if elapsed >= self.thresholds.archive_s:
            return FreshnessAction.ARCHIVE
        if elapsed >= self.thresholds.pause_s:
            if current_status == enums.AgentSessionStatus.STALE_COMPACT:
                return FreshnessAction.KEEP_ACTIVE
            return FreshnessAction.PAUSE_SANDBOX
        if elapsed >= self.thresholds.idle_notice_s:
            if current_status == enums.AgentSessionStatus.STALE_IDLE:
                return FreshnessAction.KEEP_ACTIVE
            return FreshnessAction.NOTIFY_IDLE
        return FreshnessAction.KEEP_ACTIVE


@dataclass
class FreshnessRunner:
    """Walks ``agent_sessions``, evaluates the policy, applies the
    transition + emits audit. Optional side-effect callbacks for the
    sandbox pause / destroy. Pure in-process; ARQ wire-up is M2."""

    policy: FreshnessPolicy = field(default_factory=FreshnessPolicy)
    audit_sink: AuditSink = field(default_factory=NullAuditSink)
    on_pause: Callable[[str], Awaitable[None]] | None = None
    on_archive: Callable[[str], Awaitable[None]] | None = None

    async def run_once(
        self, db: AsyncSession, *, now: datetime | None = None
    ) -> dict[FreshnessAction, int]:
        """One sweep. Returns the count of sessions per action."""
        now_ts = now or datetime.now(tz=UTC)
        counts: dict[FreshnessAction, int] = {a: 0 for a in FreshnessAction}

        sessions = (
            (
                await db.execute(
                    select(models.AgentSession).where(
                        models.AgentSession.status != enums.AgentSessionStatus.ARCHIVED
                    )
                )
            )
            .scalars()
            .all()
        )

        for session in sessions:
            action = self.policy.evaluate(
                last_active_at=session.last_active_at,
                current_status=session.status,
                now=now_ts,
            )
            counts[action] += 1
            if action == FreshnessAction.KEEP_ACTIVE:
                continue
            await self._apply(db, session, action)

        await db.flush()
        logger.info(
            "freshness.run_once",
            keep_active=counts[FreshnessAction.KEEP_ACTIVE],
            notify_idle=counts[FreshnessAction.NOTIFY_IDLE],
            pause_sandbox=counts[FreshnessAction.PAUSE_SANDBOX],
            archive=counts[FreshnessAction.ARCHIVE],
        )
        return counts

    async def _apply(
        self,
        db: AsyncSession,
        session: models.AgentSession,
        action: FreshnessAction,
    ) -> None:
        if action == FreshnessAction.NOTIFY_IDLE:
            session.status = enums.AgentSessionStatus.STALE_IDLE
            await self._emit_audit(
                session, action_name="session.idle_notice", outcome=enums.AuditOutcome.OK
            )
        elif action == FreshnessAction.PAUSE_SANDBOX:
            session.status = enums.AgentSessionStatus.STALE_COMPACT
            if self.on_pause is not None:
                await self.on_pause(session.id)
            await self._emit_audit(
                session, action_name="session.pause", outcome=enums.AuditOutcome.OK
            )
        elif action == FreshnessAction.ARCHIVE:
            session.status = enums.AgentSessionStatus.ARCHIVED
            if self.on_archive is not None:
                await self.on_archive(session.id)
            await self._emit_audit(
                session, action_name="session.archive", outcome=enums.AuditOutcome.OK
            )

    async def _emit_audit(
        self,
        session: models.AgentSession,
        *,
        action_name: str,
        outcome: enums.AuditOutcome,
    ) -> None:
        await self.audit_sink.log(
            AuditEvent(
                action=action_name,
                actor_type=enums.AuditActorType.SYSTEM,
                outcome=outcome,
                scope={
                    "project_id": session.project_id,
                    "workspace_id": session.workspace_id,
                    "session_id": session.id,
                },
                subject={"new_status": session.status.value},
            )
        )
