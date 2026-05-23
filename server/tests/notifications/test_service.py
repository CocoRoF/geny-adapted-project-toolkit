"""Unit tests for NotificationService + channels."""

from __future__ import annotations

import pytest

from gapt_server.domains.notifications import (
    DiscordWebhookChannel,
    InMemoryChannel,
    NotificationKind,
    NotificationService,
    SlackWebhookChannel,
)


@pytest.mark.asyncio
async def test_emit_appends_to_actor_ring() -> None:
    svc = NotificationService(channels=[InMemoryChannel()])
    await svc.emit(
        kind=NotificationKind.DEPLOY_SUCCESS,
        title="ok",
        body="deployed",
        actor_id="u1",
    )
    items = await svc.list_for("u1")
    assert len(items) == 1
    assert items[0].title == "ok"
    assert items[0].kind == NotificationKind.DEPLOY_SUCCESS


@pytest.mark.asyncio
async def test_broadcast_visible_to_every_actor() -> None:
    svc = NotificationService(channels=[InMemoryChannel()])
    await svc.emit(
        kind=NotificationKind.SYSTEM,
        title="maint",
        body="we are upgrading",
        actor_id=None,  # broadcast
    )
    items_u1 = await svc.list_for("u1")
    items_u2 = await svc.list_for("u2")
    assert len(items_u1) == 1
    assert len(items_u2) == 1
    # Same notification id surfaced to both actors via broadcast bucket.
    assert items_u1[0].id == items_u2[0].id


@pytest.mark.asyncio
async def test_slack_channel_posts_signed_body() -> None:
    seen: list[tuple[str, dict]] = []

    async def fake_poster(url: str, body: dict) -> int:
        seen.append((url, body))
        return 200

    svc = NotificationService(
        channels=[SlackWebhookChannel("https://hooks.slack/x", poster=fake_poster)],
    )
    await svc.emit(
        kind=NotificationKind.DEPLOY_FAILED,
        title="deploy failed",
        body="something broke",
        actor_id="u1",
        project_id="p1",
        severity="error",
    )
    assert len(seen) == 1
    url, body = seen[0]
    assert url == "https://hooks.slack/x"
    assert "deploy failed" in body["text"]
    assert body["attachments"][0]["color"] == "#dc2626"
    # The fields array carries the project context.
    fields = body["attachments"][0]["fields"]
    assert any(f["title"] == "project" and f["value"] == "p1" for f in fields)


@pytest.mark.asyncio
async def test_discord_channel_uses_embed() -> None:
    seen: list[tuple[str, dict]] = []

    async def fake_poster(url: str, body: dict) -> int:
        seen.append((url, body))
        return 204

    svc = NotificationService(
        channels=[DiscordWebhookChannel("https://discord.com/hook", poster=fake_poster)],
    )
    await svc.emit(
        kind=NotificationKind.DEPLOY_SUCCESS,
        title="prod deployed",
        body="v1.2.3 is live",
        actor_id="u1",
        project_id="p1",
    )
    assert len(seen) == 1
    body = seen[0][1]
    assert "prod deployed" in body["content"]
    assert body["embeds"][0]["title"] == "deploy.success"


@pytest.mark.asyncio
async def test_channel_failure_does_not_crash_emit() -> None:
    async def bad_poster(_url: str, _body: dict) -> int:
        return 500

    svc = NotificationService(
        channels=[SlackWebhookChannel("https://hooks.slack/x", poster=bad_poster)],
    )
    # Should not raise — service swallows + logs.
    n = await svc.emit(
        kind=NotificationKind.SYSTEM,
        title="t",
        body="b",
        actor_id="u1",
    )
    # The ring buffer still captured the notification even though the
    # outbound channel returned 500.
    items = await svc.list_for("u1")
    assert len(items) == 1
    assert items[0].id == n.id


@pytest.mark.asyncio
async def test_ring_buffer_capped_at_50() -> None:
    svc = NotificationService()
    for i in range(60):
        await svc.emit(
            kind=NotificationKind.SYSTEM,
            title=f"n{i}",
            body="",
            actor_id="u1",
        )
    items = await svc.list_for("u1")
    assert len(items) == 50
    # Most-recent first; the latest 50 means n10..n59.
    assert items[0].title == "n59"
    assert items[-1].title == "n10"
