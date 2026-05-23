"""GithubDeviceFlow — start + poll + revoke with httpx MockTransport."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from gapt_server.domains.auth.github_oauth import (
    GithubDeviceFlow,
    GithubOAuthError,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _flow(handler: Callable[[httpx.Request], httpx.Response]) -> GithubDeviceFlow:
    transport = httpx.MockTransport(handler)
    return GithubDeviceFlow(
        client_id="Iv1.fake",
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, headers={"Accept": "application/json"}
        ),
    )


# ────────────────────────────────────────────────────────── start ──


@pytest.mark.asyncio
async def test_start_returns_device_flow_session() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "device_code": "DEVICE-CODE",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
        )

    flow = _flow(handler)
    session = await flow.start()
    assert session.device_code == "DEVICE-CODE"
    assert session.user_code == "ABCD-EFGH"
    assert session.interval_s == 5

    # Verify the start request payload.
    assert seen[0].method == "POST"
    body = bytes(seen[0].content).decode()
    assert "client_id=Iv1.fake" in body
    assert "scope=repo%2Cworkflow" in body


@pytest.mark.asyncio
async def test_start_malformed_response_raises() -> None:
    flow = _flow(lambda req: httpx.Response(200, json={"only_user_code": "x"}))
    with pytest.raises(GithubOAuthError) as exc:
        await flow.start()
    assert exc.value.code == "auth.github.malformed_response"


@pytest.mark.asyncio
async def test_start_5xx_raises_transport() -> None:
    flow = _flow(lambda req: httpx.Response(503, text="github down"))
    with pytest.raises(GithubOAuthError) as exc:
        await flow.start()
    assert exc.value.code == "auth.github.transport"


# ────────────────────────────────────────────────────────── poll ──


def _ok_start_session(interval_s: int = 5) -> dict[str, object]:
    return {
        "device_code": "DEVICE-CODE",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": interval_s,
    }


@pytest.mark.asyncio
async def test_poll_returns_token_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("device/code"):
            return httpx.Response(200, json=_ok_start_session())
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_LIVE-TOKEN",
                "token_type": "bearer",
                "scope": "repo workflow",
            },
        )

    flow = _flow(handler)
    session = await flow.start()
    issued = await flow.poll_once(session)
    assert issued is not None
    assert issued.access_token == "ghu_LIVE-TOKEN"
    assert issued.scope == "repo workflow"


@pytest.mark.asyncio
async def test_poll_returns_none_for_pending() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("device/code"):
            return httpx.Response(200, json=_ok_start_session())
        return httpx.Response(200, json={"error": "authorization_pending"})

    flow = _flow(handler)
    session = await flow.start()
    issued = await flow.poll_once(session)
    assert issued is None


@pytest.mark.asyncio
async def test_poll_returns_none_for_slow_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("device/code"):
            return httpx.Response(200, json=_ok_start_session())
        return httpx.Response(200, json={"error": "slow_down"})

    flow = _flow(handler)
    session = await flow.start()
    assert await flow.poll_once(session) is None


@pytest.mark.asyncio
async def test_poll_raises_on_access_denied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("device/code"):
            return httpx.Response(200, json=_ok_start_session())
        return httpx.Response(200, json={"error": "access_denied"})

    flow = _flow(handler)
    session = await flow.start()
    with pytest.raises(GithubOAuthError) as exc:
        await flow.poll_once(session)
    assert exc.value.code == "auth.github.denied"


@pytest.mark.asyncio
async def test_poll_raises_on_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("device/code"):
            return httpx.Response(200, json=_ok_start_session())
        return httpx.Response(200, json={"error": "expired_token"})

    flow = _flow(handler)
    session = await flow.start()
    with pytest.raises(GithubOAuthError) as exc:
        await flow.poll_once(session)
    assert exc.value.code == "auth.github.device_code_expired"


# ─────────────────────────────────────── poll_until_complete ──


@pytest.mark.asyncio
async def test_poll_until_complete_returns_token() -> None:
    attempts = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("device/code"):
            return httpx.Response(200, json=_ok_start_session(interval_s=1))
        attempts["i"] += 1
        if attempts["i"] < 3:
            return httpx.Response(200, json={"error": "authorization_pending"})
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_FINAL",
                "token_type": "bearer",
                "scope": "repo",
            },
        )

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    flow = _flow(handler)
    session = await flow.start()
    issued = await flow.poll_until_complete(session, sleep=fake_sleep)
    assert issued.access_token == "ghu_FINAL"
    assert attempts["i"] == 3
    assert sleep_calls == [1, 1]


# ─────────────────────────────────────────────────────── revoke ──


@pytest.mark.asyncio
async def test_revoke_happy_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    flow = _flow(handler)
    await flow.revoke(token="ghu_X", client_secret="client-secret")
    assert seen[0].method == "DELETE"
    # Authorization header is set automatically by httpx auth=
    assert seen[0].headers.get("Authorization", "").startswith("Basic ")


@pytest.mark.asyncio
async def test_revoke_404_is_idempotent() -> None:
    flow = _flow(lambda req: httpx.Response(404))
    # Should NOT raise — revoking an already-gone token is fine.
    await flow.revoke(token="ghu_dead", client_secret="cs")


@pytest.mark.asyncio
async def test_revoke_other_error_raises() -> None:
    flow = _flow(lambda req: httpx.Response(500))
    with pytest.raises(GithubOAuthError) as exc:
        await flow.revoke(token="x", client_secret="cs")
    assert exc.value.code == "auth.github.transport"
