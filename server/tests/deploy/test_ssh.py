"""RemoteSshTarget — runner protocol verified without touching SSH."""

from __future__ import annotations

import pytest

from gapt_server.domains.deploy import (
    DeployContext,
    DeployRequest,
    DeployStatusKind,
    DeployTargetError,
    RemoteSshTarget,
)
from gapt_server.domains.deploy.ssh import SshConnectionSpec


def _ctx(**ssh: object) -> DeployContext:
    request = DeployRequest(
        project_id="p1",
        environment="prod",
        version="v1",
        compose_path="compose.yml",
        env_secrets={"DB_URL": "postgres://x"},
        target_options={"ssh": ssh or {"host": "h", "user": "u", "private_key_pem": "k"}},
    )
    return DeployContext(run_id="run-1", request=request)


@pytest.mark.asyncio
async def test_deploy_success_runs_pull_then_up() -> None:
    captured: list[tuple[SshConnectionSpec, str, dict[str, str]]] = []

    async def runner(spec: SshConnectionSpec, cmd: str, env: dict[str, str]) -> tuple[int, str, str]:
        captured.append((spec, cmd, dict(env)))
        return (0, "ok\n", "")

    target = RemoteSshTarget(runner=runner)
    result = await target.deploy(_ctx())

    assert result.status is DeployStatusKind.SUCCESS
    assert any("pull" in cmd for _, cmd, _ in captured)
    assert any("up -d" in cmd for _, cmd, _ in captured)
    assert all(env.get("DB_URL") == "postgres://x" for _, _, env in captured)
    assert captured[0][0].host == "h"


@pytest.mark.asyncio
async def test_deploy_fails_when_spec_missing() -> None:
    request = DeployRequest(
        project_id="p1",
        environment="prod",
        version="v",
        target_options={},  # no ssh key
    )
    ctx = DeployContext(run_id="run-x", request=request)
    target = RemoteSshTarget(runner=_should_not_run)

    with pytest.raises(DeployTargetError) as exc:
        await target.deploy(ctx)
    assert exc.value.code == "deploy.ssh.spec_missing"


@pytest.mark.asyncio
async def test_runner_transport_failure_becomes_exec_code() -> None:
    async def runner(spec: SshConnectionSpec, cmd: str, env: dict[str, str]) -> tuple[int, str, str]:
        raise DeployTargetError("deploy.ssh.transport", "connection refused")

    target = RemoteSshTarget(runner=runner)
    result = await target.deploy(_ctx())

    assert result.status is DeployStatusKind.FAILED
    assert result.exec_code == "deploy.ssh.transport"


async def _should_not_run(
    spec: SshConnectionSpec, cmd: str, env: dict[str, str]
) -> tuple[int, str, str]:
    raise AssertionError(f"runner unexpectedly invoked: {cmd}")
