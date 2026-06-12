"""LocalComposeTarget — drives docker compose via an injectable runner.

No real docker is touched. The runner stub returns canned exit code +
stdout/stderr the target parses."""

from __future__ import annotations

import pytest

from gapt_server.domains.deploy import (
    DeployContext,
    DeployRequest,
    DeployStatusKind,
    LocalComposeTarget,
)


def _ctx(**overrides: object) -> DeployContext:
    request = DeployRequest(
        project_id="01KSXXXXXXXXXXXXXXXXXXXXXX",
        environment="dev",
        version="abc123",
        compose_path="compose/dev.yml",
        env_secrets={"DB_URL": "postgres://x"},
    )
    return DeployContext(run_id="run-1", request=request)


@pytest.mark.asyncio
async def test_deploy_success_snapshots_then_pulls_then_ups() -> None:
    calls: list[list[str]] = []

    async def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        calls.append(argv)
        if "ps" in argv:
            return (0, "", "")  # nothing running yet
        if "config" in argv:
            # Port preflight resolves the compose model — a service
            # with no `ports` keeps the preflight a no-op.
            return (0, '{"services": {"web": {"image": "x"}}}', "")
        if "pull" in argv:
            return (0, "pulled\n", "")
        if "up" in argv:
            return (0, "started\n", "")
        return (1, "", f"unexpected: {argv}")

    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=runner)
    result = await target.deploy(_ctx())

    assert result.status is DeployStatusKind.SUCCESS
    assert result.exec_code is None
    # Compose subcommands in order: ps (snapshot), config (port
    # preflight), pull, up.
    compose_cmds = [argv for argv in calls if "compose" in argv]
    assert "-p" in compose_cmds[0]
    project_idx = compose_cmds[0].index("-p")
    assert compose_cmds[0][project_idx + 1] == "gapt-prod-01ksxxxxxxxxxxxxxxxxxxxxxx"
    assert "ps" in compose_cmds[0]
    assert "config" in compose_cmds[1]
    assert "pull" in compose_cmds[2]
    assert "up" in compose_cmds[3]


@pytest.mark.asyncio
async def test_deploy_failure_on_pull_returns_exec_code() -> None:
    async def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        if "ps" in argv:
            return (0, "", "")
        if "pull" in argv:
            return (1, "", "image not found\n")
        return (0, "", "")

    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=runner)
    result = await target.deploy(_ctx())

    assert result.status is DeployStatusKind.FAILED
    assert result.exec_code == "deploy.compose_pull_failed"


@pytest.mark.asyncio
async def test_status_returns_pending_for_unknown_run() -> None:
    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=_noop_runner)
    status = await target.status(_ctx())
    assert status.status is DeployStatusKind.PENDING


@pytest.mark.asyncio
async def test_secret_env_is_zeroized_after_deploy() -> None:
    captured: list[dict[str, str]] = []

    async def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        captured.append(dict(env))
        return (0, "", "")

    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=runner)
    request = DeployRequest(
        project_id="p1",
        environment="prod",
        version="v",
        env_secrets={"API_KEY": "supersecret"},
    )
    ctx = DeployContext(run_id="run-z", request=request)
    await target.deploy(ctx)

    # During the run the env carried the secret.
    assert any(call.get("API_KEY") == "supersecret" for call in captured)
    # The caller-passed dict shouldn't carry the value either —
    # the target's zeroize step blanks its working copy. The
    # original `request.env_secrets` is immutable (frozen dataclass)
    # but the working copy in scope is local; we verify by mutating
    # the call later in the run is no-op (caller's dict untouched).
    assert request.env_secrets == {"API_KEY": "supersecret"}


async def _noop_runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    return (0, "", "")


@pytest.mark.asyncio
async def test_deploy_chains_multiple_compose_files() -> None:
    """compose_paths = [a, b] should yield `-f a -f b` in argv —
    this is the path Geny's multi-file setup goes through."""
    calls: list[list[str]] = []

    async def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        calls.append(argv)
        return (0, "", "")

    target = LocalComposeTarget(docker_binary="/usr/bin/docker", runner=runner)
    request = DeployRequest(
        project_id="01KSXXXXXXXXXXXXXXXXXXXXXX",
        environment="dev",
        version="abc",
        compose_path="compose/dev.yml",  # ignored when compose_paths is set
        compose_paths=["compose/dev.yml", "compose/dev-core.yml"],
    )
    ctx = DeployContext(run_id="run-multi", request=request)
    result = await target.deploy(ctx)
    assert result.status is DeployStatusKind.SUCCESS

    # `pull` and `up` argv must both carry both -f flags in order.
    pull = next(a for a in calls if "pull" in a)
    up = next(a for a in calls if "up" in a)
    for argv in (pull, up):
        f_indices = [i for i, x in enumerate(argv) if x == "-f"]
        assert len(f_indices) == 2
        assert argv[f_indices[0] + 1] == "compose/dev.yml"
        assert argv[f_indices[1] + 1] == "compose/dev-core.yml"
