"""Notification dispatcher.

The service keeps an in-memory ring buffer per actor (last 50
notifications) so the web bell has something to render even when the
user reloads. Each notification is also pushed to every registered
channel — the in-memory channel is what the bell reads; Slack /
Discord channels are operator-configured webhooks.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

from gapt_server.db.ulid import new_ulid

if TYPE_CHECKING:
    from gapt_server.domains.notifications.channel import Channel


logger = structlog.get_logger(__name__)

# Per-actor history depth. Reading the bell beyond this means hitting
# the audit log instead.
_RING_CAPACITY = 50


class NotificationKind(StrEnum):
    DEPLOY_SUCCESS = "deploy.success"
    DEPLOY_FAILED = "deploy.failed"
    DEPLOY_ROLLED_BACK = "deploy.rolled_back"
    POLICY_DENIED = "policy.denied"
    CI_GREEN = "ci.green"
    CI_FAILED = "ci.failed"
    COST_CAP_NEAR = "cost.cap_near"
    SYSTEM = "system"


@dataclass(frozen=True)
class Notification:
    id: str
    kind: NotificationKind
    title: str
    body: str
    # Audience scope. `actor_id=None` means broadcast (every channel
    # gets it, every actor's ring buffer captures it).
    actor_id: str | None
    project_id: str | None = None
    workspace_id: str | None = None
    severity: str = "info"  # info / warn / error
    ts: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "body": self.body,
            "actor_id": self.actor_id,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "severity": self.severity,
            "ts": self.ts,
            "details": dict(self.details),
        }


class NotificationService:
    def __init__(self, channels: list[Channel] | None = None) -> None:
        self._channels: list[Channel] = list(channels or [])
        self._ring: dict[str | None, deque[Notification]] = {}
        self._lock = asyncio.Lock()

    def add_channel(self, channel: Channel) -> None:
        self._channels.append(channel)

    async def emit(
        self,
        *,
        kind: NotificationKind,
        title: str,
        body: str,
        actor_id: str | None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        severity: str = "info",
        details: dict[str, Any] | None = None,
    ) -> Notification:
        n = Notification(
            id=new_ulid(),
            kind=kind,
            title=title,
            body=body,
            actor_id=actor_id,
            project_id=project_id,
            workspace_id=workspace_id,
            severity=severity,
            details=details or {},
        )
        async with self._lock:
            buf = self._ring.setdefault(actor_id, deque(maxlen=_RING_CAPACITY))
            buf.append(n)
        for ch in self._channels:
            try:
                await ch.deliver(n)
            except Exception as exc:
                # A misconfigured webhook should not crash the
                # caller; log + continue.
                logger.warning(
                    "notification.channel_failed",
                    channel=type(ch).__name__,
                    notification_id=n.id,
                    error=str(exc),
                )
        return n

    async def list_for(self, actor_id: str, *, limit: int = 50) -> list[Notification]:
        async with self._lock:
            mine = list(self._ring.get(actor_id, deque()))
            broadcast = list(self._ring.get(None, deque()))
        merged = sorted(mine + broadcast, key=lambda x: x.ts, reverse=True)
        return merged[:limit]
