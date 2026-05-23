"""AskpassTokenStore — issue / exchange / revoke / gc lifecycle."""

from __future__ import annotations

import asyncio

import pytest

from gapt_server.domains.git.askpass import AskpassError, AskpassTokenStore


@pytest.mark.asyncio
async def test_issue_returns_random_id_and_ttl() -> None:
    store = AskpassTokenStore(default_ttl_s=30.0)
    token = await store.issue(secret="ghu_live")
    assert token.secret == "ghu_live"
    assert len(token.token_id) >= 30  # token_urlsafe(32) → ~43 chars
    assert token.expires_at > token.issued_at


@pytest.mark.asyncio
async def test_two_issues_return_different_ids() -> None:
    store = AskpassTokenStore()
    a = await store.issue(secret="x")
    b = await store.issue(secret="x")
    assert a.token_id != b.token_id


@pytest.mark.asyncio
async def test_empty_secret_refused() -> None:
    store = AskpassTokenStore()
    with pytest.raises(AskpassError) as exc:
        await store.issue(secret="")
    assert exc.value.code == "auth.askpass.unknown"


@pytest.mark.asyncio
async def test_exchange_returns_secret_once() -> None:
    store = AskpassTokenStore()
    token = await store.issue(secret="ghu_LIVE")
    assert await store.exchange(token.token_id) == "ghu_LIVE"
    # Second exchange fails.
    with pytest.raises(AskpassError) as exc:
        await store.exchange(token.token_id)
    assert exc.value.code == "auth.askpass.consumed"


@pytest.mark.asyncio
async def test_unknown_token_raises() -> None:
    store = AskpassTokenStore()
    with pytest.raises(AskpassError) as exc:
        await store.exchange("nope-not-a-real-id")
    assert exc.value.code == "auth.askpass.unknown"


@pytest.mark.asyncio
async def test_expired_token_refused() -> None:
    store = AskpassTokenStore(default_ttl_s=0.01)
    token = await store.issue(secret="x")
    await asyncio.sleep(0.05)
    with pytest.raises(AskpassError) as exc:
        await store.exchange(token.token_id)
    assert exc.value.code == "auth.askpass.expired"


@pytest.mark.asyncio
async def test_revoke_drops_entry() -> None:
    store = AskpassTokenStore()
    token = await store.issue(secret="x")
    await store.revoke(token.token_id)
    with pytest.raises(AskpassError) as exc:
        await store.exchange(token.token_id)
    assert exc.value.code == "auth.askpass.unknown"


@pytest.mark.asyncio
async def test_gc_clears_expired_and_consumed() -> None:
    store = AskpassTokenStore(default_ttl_s=0.01)
    expired = await store.issue(secret="x")
    consumed = await store.issue(secret="y", ttl_s=60.0)
    await store.exchange(consumed.token_id)
    await asyncio.sleep(0.05)
    removed = await store.gc()
    assert removed == 2
    # Subsequent exchange on both should now be `unknown`.
    with pytest.raises(AskpassError) as exc:
        await store.exchange(expired.token_id)
    assert exc.value.code == "auth.askpass.unknown"


@pytest.mark.asyncio
async def test_gc_keeps_live_entries() -> None:
    store = AskpassTokenStore(default_ttl_s=60.0)
    live = await store.issue(secret="x")
    removed = await store.gc()
    assert removed == 0
    # Still exchangeable.
    assert await store.exchange(live.token_id) == "x"
