"""`WebhookTarget` — HMAC-signed POST to a user-defined URL.

The user registers `{url, secret}` in project settings. On deploy we
POST a JSON payload of the deploy intent with an `X-GAPT-Signature`
header computed as `hex(HMAC-SHA256(secret, body))`. The webhook
endpoint is expected to verify, kick off whatever its own deployer
is, and return JSON like `{"status": "success" | "failed",
"version": "..."}`.

Status / rollback are mapped to additional POSTs with `action`
discriminator so a single webhook URL covers all three verbs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from gapt_server.domains.deploy.protocol import (
    DeployContext,
    DeployResult,
    DeployStatus,
    DeployStatusKind,
    DeployTargetError,
    RollbackResult,
)

WebhookPoster = Callable[
    [str, dict[str, Any], dict[str, str]],
    Awaitable[tuple[int, dict[str, Any]]],
]


async def _default_poster(
    url: str, body: dict[str, Any], headers: dict[str, str]
) -> tuple[int, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=headers)
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {"raw": resp.text[:2000]}
    return resp.status_code, parsed if isinstance(parsed, dict) else {"data": parsed}


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass
class _RunState:
    status: DeployStatusKind = DeployStatusKind.PENDING
    log_tail: str = ""
    exec_code: str | None = None
    finished_at: datetime | None = None


@dataclass
class WebhookTarget:
    name: str = "webhook"
    poster: WebhookPoster = field(default=_default_poster)
    _runs: dict[str, _RunState] = field(default_factory=dict)

    def _config(self, ctx: DeployContext) -> tuple[str, str]:
        opts = ctx.request.target_options.get("webhook")
        if not isinstance(opts, dict):
            raise DeployTargetError(
                "deploy.webhook.spec_missing",
                "request.target_options['webhook'] must be a mapping",
            )
        url = opts.get("url")
        secret = opts.get("secret")
        if not isinstance(url, str) or not url:
            raise DeployTargetError(
                "deploy.webhook.spec_missing", "webhook.url is required"
            )
        if not isinstance(secret, str) or not secret:
            raise DeployTargetError(
                "deploy.webhook.spec_missing", "webhook.secret is required for HMAC"
            )
        return url, secret

    async def _call(
        self,
        url: str,
        secret: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        sig = _sign(secret, body_bytes)
        headers = {
            "Content-Type": "application/json",
            "X-GAPT-Signature": sig,
        }
        return await self.poster(url, payload, headers)

    async def deploy(self, ctx: DeployContext) -> DeployResult:
        url, secret = self._config(ctx)
        state = _RunState(status=DeployStatusKind.RUNNING)
        self._runs[ctx.run_id] = state

        payload: dict[str, Any] = {
            "action": "deploy",
            "run_id": ctx.run_id,
            "project_id": ctx.request.project_id,
            "environment": ctx.request.environment,
            "version": ctx.request.version,
            "compose_path": ctx.request.compose_path,
            # Secret env *names* travel as a hint (we never POST the
            # values — the webhook owner is responsible for fetching
            # those from its own store). This keeps the webhook URL
            # safe to log.
            "env_keys": sorted(ctx.request.env_secrets.keys()),
        }
        try:
            status_code, body = await self._call(url, secret, payload)
        except httpx.HTTPError as exc:
            state.status = DeployStatusKind.FAILED
            state.exec_code = "deploy.webhook.transport"
            state.log_tail = f"webhook transport error: {exc}"[:2000]
            state.finished_at = datetime.now(tz=UTC)
            return DeployResult(
                run_id=ctx.run_id,
                status=state.status,
                log=state.log_tail,
                exec_code=state.exec_code,
            )

        state.log_tail = json.dumps(body, separators=(",", ":"))[:2000]
        if status_code >= 400:
            state.status = DeployStatusKind.FAILED
            state.exec_code = f"deploy.webhook.http_{status_code}"
            state.finished_at = datetime.now(tz=UTC)
            return DeployResult(
                run_id=ctx.run_id,
                status=state.status,
                log=state.log_tail,
                exec_code=state.exec_code,
            )

        reported = str(body.get("status", "success")).lower()
        terminal = (
            DeployStatusKind.SUCCESS
            if reported in {"success", "ok", "deployed"}
            else DeployStatusKind.FAILED
        )
        state.status = terminal
        if terminal is DeployStatusKind.FAILED:
            state.exec_code = "deploy.webhook.reported_failure"
        state.finished_at = datetime.now(tz=UTC)
        return DeployResult(
            run_id=ctx.run_id,
            status=state.status,
            log=state.log_tail,
            exec_code=state.exec_code,
        )

    async def status(self, ctx: DeployContext) -> DeployStatus:
        state = self._runs.get(ctx.run_id)
        if state is None:
            return DeployStatus(run_id=ctx.run_id, status=DeployStatusKind.PENDING)
        return DeployStatus(
            run_id=ctx.run_id,
            status=state.status,
            log_tail=state.log_tail,
            exec_code=state.exec_code,
            finished_at=state.finished_at,
        )

    async def rollback(self, ctx: DeployContext, *, to_version: str) -> RollbackResult:
        url, secret = self._config(ctx)
        payload: dict[str, Any] = {
            "action": "rollback",
            "run_id": ctx.run_id,
            "project_id": ctx.request.project_id,
            "environment": ctx.request.environment,
            "to_version": to_version,
        }
        try:
            status_code, body = await self._call(url, secret, payload)
        except httpx.HTTPError as exc:
            return RollbackResult(
                run_id=ctx.run_id,
                status=DeployStatusKind.FAILED,
                restored_version=to_version,
                log=f"webhook transport error: {exc}"[:2000],
                exec_code="deploy.webhook.transport",
            )
        if status_code >= 400:
            return RollbackResult(
                run_id=ctx.run_id,
                status=DeployStatusKind.FAILED,
                restored_version=to_version,
                log=json.dumps(body, separators=(",", ":"))[:2000],
                exec_code=f"deploy.webhook.http_{status_code}",
            )
        return RollbackResult(
            run_id=ctx.run_id,
            status=DeployStatusKind.ROLLED_BACK,
            restored_version=to_version,
            log=json.dumps(body, separators=(",", ":"))[:2000],
        )
