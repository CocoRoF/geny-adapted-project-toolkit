"""Notifications domain — D-notify.

In-memory dispatcher + pluggable channels (`InMemoryChannel` for the
web bell, `SlackWebhookChannel` / `DiscordWebhookChannel` for outbound
webhooks). M1 keeps everything ephemeral — no DB table, no per-user
subscriptions yet. The Slack/Discord URLs are operator-set via env so
the operator can opt in immediately without UI plumbing.

Subscriptions UI + DB persistence land in Cycle 4.10 (dogfood) once we
know which surfaces we actually want to receive notifications for.
"""

from gapt_server.domains.notifications.channel import (
    Channel,
    DiscordWebhookChannel,
    InMemoryChannel,
    NotificationError,
    SlackWebhookChannel,
)
from gapt_server.domains.notifications.service import (
    Notification,
    NotificationKind,
    NotificationService,
)

__all__ = [
    "Channel",
    "DiscordWebhookChannel",
    "InMemoryChannel",
    "Notification",
    "NotificationError",
    "NotificationKind",
    "NotificationService",
    "SlackWebhookChannel",
]
