"""Notification feed endpoints.

- `GET /api/notifications` — list the current actor's bell history
  (merged with broadcasts).
- `POST /api/notifications/test` — emit a test notification through
  every registered channel. Operators use this to confirm Slack /
  Discord webhooks are wired correctly without waiting for a real
  deploy.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from gapt_server.container import get_notifications
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.notifications import NotificationKind, NotificationService
from gapt_server.routers.auth import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class TestNotificationRequest(BaseModel):
    kind: NotificationKind = NotificationKind.SYSTEM
    title: str = "Test notification"
    body: str = "If you see this in Slack/Discord, your webhook is wired."
    severity: str = "info"


@router.get("", response_model=list[dict[str, Any]])
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    notifications: NotificationService = Depends(get_notifications),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[dict[str, Any]]:
    items = await notifications.list_for(user.id, limit=limit)
    return [n.to_wire() for n in items]


@router.post("/test", response_model=dict[str, Any])
async def emit_test_notification(
    payload: TestNotificationRequest,
    notifications: NotificationService = Depends(get_notifications),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    n = await notifications.emit(
        kind=payload.kind,
        title=payload.title,
        body=payload.body,
        actor_id=user.id,
        severity=payload.severity,
    )
    return n.to_wire()
