"""Notification channels.

Three implementations:

- `InMemoryChannel` — pure no-op; the ring buffer in the service is
  what the web bell actually reads. Kept as a Channel impl so tests
  can assert delivery uniformly.
- `SlackWebhookChannel` — POSTs to a Slack-format incoming webhook.
- `DiscordWebhookChannel` — POSTs to a Discord webhook.

Both webhook channels accept an injectable `poster` so tests don't
hit the network. The default poster uses `httpx.AsyncClient`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from gapt_server.domains.notifications.service import Notification


class NotificationError(RuntimeError):
    """Channel failed to deliver. Service swallows + logs but tests
    sometimes want the type."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Channel(Protocol):
    name: str

    async def deliver(self, notification: Notification) -> None: ...


WebhookPoster = Callable[[str, dict[str, Any]], Awaitable[int]]


async def _default_poster(url: str, body: dict[str, Any]) -> int:
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(url, json=body)
        return resp.status_code


_SEVERITY_EMOJI: dict[str, str] = {
    "info": ":information_source:",
    "warn": ":warning:",
    "error": ":x:",
}


class InMemoryChannel:
    """No-op channel — the service ring buffer is the actual storage.

    Useful as a marker that "the bell is active" in tests + as a place
    to attach future hooks (e.g. WebSocket fanout in M2)."""

    name: str = "memory"

    async def deliver(self, notification: Notification) -> None:
        return


class SlackWebhookChannel:
    """POST notification to a Slack incoming webhook URL."""

    name: str = "slack"

    def __init__(self, webhook_url: str, *, poster: WebhookPoster | None = None) -> None:
        self._url = webhook_url
        self._poster = poster or _default_poster

    async def deliver(self, notification: Notification) -> None:
        emoji = _SEVERITY_EMOJI.get(notification.severity, ":bell:")
        body: dict[str, Any] = {
            "text": f"{emoji} *{notification.title}*\n{notification.body}",
            "attachments": [
                {
                    "color": _slack_color(notification.severity),
                    "fields": [
                        {"title": "kind", "value": notification.kind.value, "short": True},
                    ]
                    + (
                        [{"title": "project", "value": notification.project_id, "short": True}]
                        if notification.project_id
                        else []
                    ),
                }
            ],
        }
        status = await self._poster(self._url, body)
        if status >= 400:
            raise NotificationError(
                "notification.slack.http_status",
                f"slack webhook returned {status}",
            )


class DiscordWebhookChannel:
    """POST notification to a Discord webhook URL."""

    name: str = "discord"

    def __init__(self, webhook_url: str, *, poster: WebhookPoster | None = None) -> None:
        self._url = webhook_url
        self._poster = poster or _default_poster

    async def deliver(self, notification: Notification) -> None:
        emoji = _SEVERITY_EMOJI.get(notification.severity, ":bell:")
        body: dict[str, Any] = {
            "content": f"{emoji} **{notification.title}**\n{notification.body}",
            "embeds": [
                {
                    "title": notification.kind.value,
                    "description": notification.body,
                    "color": _discord_color(notification.severity),
                }
            ],
        }
        status = await self._poster(self._url, body)
        if status >= 400:
            raise NotificationError(
                "notification.discord.http_status",
                f"discord webhook returned {status}",
            )


def _slack_color(severity: str) -> str:
    if severity == "error":
        return "#dc2626"
    if severity == "warn":
        return "#d97706"
    return "#2563eb"


def _discord_color(severity: str) -> int:
    if severity == "error":
        return 0xDC2626
    if severity == "warn":
        return 0xD97706
    return 0x2563EB
