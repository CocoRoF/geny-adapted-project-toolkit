"""WebhookTarget — HMAC signing + status mapping."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from gapt_server.domains.deploy import (
    DeployContext,
    DeployRequest,
    DeployStatusKind,
    DeployTargetError,
    WebhookTarget,
)


SECRET = "shhhhh"


def _ctx(**overrides: object) -> DeployContext:
    target_options = overrides.get(
        "target_options",
        {"webhook": {"url": "https://example.com/deploy", "secret": SECRET}},
    )
    request = DeployRequest(
        project_id="p1",
        environment="prod",
        version="v1",
        compose_path="compose.yml",
        env_secrets={"DB_URL": "postgres://x"},
        target_options=target_options,
    )
    return DeployContext(run_id="run-w", request=request)


@pytest.mark.asyncio
async def test_deploy_posts_with_hmac_signature() -> None:
    captured: dict[str, Any] = {}

    async def poster(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return (200, {"status": "success"})

    target = WebhookTarget(poster=poster)
    result = await target.deploy(_ctx())

    assert result.status is DeployStatusKind.SUCCESS
    assert captured["url"] == "https://example.com/deploy"
    assert captured["body"]["action"] == "deploy"
    assert "DB_URL" in captured["body"]["env_keys"]
    # The secret values are never POSTed — just the env *keys*.
    assert "postgres://x" not in json.dumps(captured["body"])

    # Verify the signature matches body-serialised SHA-256.
    body_bytes = json.dumps(captured["body"], separators=(",", ":"), sort_keys=True).encode("utf-8")
    expected = hmac.new(SECRET.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    assert captured["headers"]["X-GAPT-Signature"] == expected


@pytest.mark.asyncio
async def test_deploy_http_400_becomes_exec_code() -> None:
    async def poster(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        return (502, {"error": "upstream"})

    target = WebhookTarget(poster=poster)
    result = await target.deploy(_ctx())

    assert result.status is DeployStatusKind.FAILED
    assert result.exec_code == "deploy.webhook.http_502"


@pytest.mark.asyncio
async def test_deploy_reported_failure_marks_failed() -> None:
    async def poster(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        return (200, {"status": "failed", "reason": "build broken"})

    target = WebhookTarget(poster=poster)
    result = await target.deploy(_ctx())

    assert result.status is DeployStatusKind.FAILED
    assert result.exec_code == "deploy.webhook.reported_failure"


@pytest.mark.asyncio
async def test_deploy_rejects_missing_spec() -> None:
    target = WebhookTarget(poster=_unused_poster)
    with pytest.raises(DeployTargetError) as exc:
        await target.deploy(_ctx(target_options={}))
    assert exc.value.code == "deploy.webhook.spec_missing"


@pytest.mark.asyncio
async def test_rollback_posts_action_rollback() -> None:
    captured: dict[str, Any] = {}

    async def poster(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        captured.update(body)
        return (200, {"status": "ok"})

    target = WebhookTarget(poster=poster)
    result = await target.rollback(_ctx(), to_version="v0")

    assert result.status is DeployStatusKind.ROLLED_BACK
    assert captured["action"] == "rollback"
    assert captured["to_version"] == "v0"


async def _unused_poster(
    url: str, body: dict[str, Any], headers: dict[str, str]
) -> tuple[int, dict[str, Any]]:
    raise AssertionError("poster should not be invoked")
