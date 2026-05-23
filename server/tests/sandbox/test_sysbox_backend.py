"""SysboxBackend unit tests with a mocked docker.DockerClient.

A real-Sysbox integration smoke is gated on the GAPT_TEST_SANDBOX=1
env var (skipped by default — CI doesn't ship sysbox-runc) and lives
in test_sysbox_real.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gapt_server.domains.sandbox import (
    MountSpec,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxRef,
    SandboxResources,
    SecurityInvariantError,
    SysboxBackend,
    SysboxBackendConfig,
)


def _build(spec_kwargs: dict | None = None) -> tuple[SysboxBackend, MagicMock]:
    client = MagicMock()
    created_container = MagicMock()
    created_container.id = "fake-container-id"
    client.containers.create.return_value = created_container
    client.containers.get.return_value = created_container
    backend = SysboxBackend(client=client)
    return backend, client


def _spec(**overrides) -> SandboxCreateSpec:
    base = {
        "project_id": "01KS900000000000000000PRJX",
        "workspace_id": "01KS900000000000000000WSP1",
        "image": "ghcr.io/cocorof/gapt-runtime:dev",
        "resources": SandboxResources(),
        "mounts": (),
        "env": {"FOO": "bar"},
        "labels": {"team": "demo"},
    }
    base.update(overrides)
    return SandboxCreateSpec(**base)


@pytest.mark.asyncio
async def test_create_passes_sysbox_runtime_and_gapt_env() -> None:
    backend, client = _build()
    ref = await backend.create(_spec())

    assert ref.backend == "sysbox"
    assert ref.container_id == "fake-container-id"

    client.containers.create.assert_called_once()
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["runtime"] == "sysbox-runc"
    assert kwargs["image"].endswith("gapt-runtime:dev")
    env = kwargs["environment"]
    assert env["GAPT_SANDBOX_ID"] == ref.id
    assert env["GAPT_PROJECT_ID"] == "01KS900000000000000000PRJX"
    assert env["GAPT_WORKSPACE_ID"] == "01KS900000000000000000WSP1"
    assert env["FOO"] == "bar"
    labels = kwargs["labels"]
    assert labels["gapt.managed"] == "true"
    assert labels["gapt.runtime"] == "sysbox-runc"
    assert labels["gapt.sandbox_id"] == ref.id
    assert labels["gapt.project_id"] == "01KS900000000000000000PRJX"
    assert labels["team"] == "demo"
    assert kwargs["volumes"] is None
    assert kwargs["cpu_quota"] == 200_000
    assert kwargs["mem_limit"] == 4 * 1024 * 1024 * 1024


@pytest.mark.asyncio
async def test_create_renders_mounts_in_docker_py_shape() -> None:
    backend, client = _build()
    spec = _spec(
        mounts=(
            MountSpec(source="/srv/seaweedfs/proj", target="/workspace"),
            MountSpec(source="/var/lib/gapt/cache", target="/cache", read_only=True),
        )
    )
    await backend.create(spec)
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["volumes"] == {
        "/srv/seaweedfs/proj": {"bind": "/workspace", "mode": "rw"},
        "/var/lib/gapt/cache": {"bind": "/cache", "mode": "ro"},
    }


@pytest.mark.asyncio
async def test_create_refuses_docker_socket_mount() -> None:
    backend, client = _build()
    spec = _spec(mounts=(MountSpec(source="/var/run/docker.sock", target="/var/run/docker.sock"),))
    with pytest.raises(SecurityInvariantError):
        await backend.create(spec)
    # docker.containers.create was never called — invariant gated upfront.
    client.containers.create.assert_not_called()


@pytest.mark.asyncio
async def test_lifecycle_calls_are_threadhopped() -> None:
    backend, client = _build()
    ref = await backend.create(_spec())

    container = client.containers.get.return_value
    container.attrs = {"State": {"Status": "running"}, "Config": {"Image": "img"}}

    await backend.start(ref)
    container.start.assert_called_once()

    info = await backend.inspect(ref)
    assert info.status == "running"

    await backend.stop(ref)
    container.stop.assert_called_once_with(timeout=10)

    await backend.destroy(ref)
    container.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_exec_in_demux_stdout_stderr() -> None:
    backend, client = _build()
    ref = await backend.create(_spec())
    container = client.containers.get.return_value

    fake = MagicMock()
    fake.exit_code = 2
    fake.output = (b"out", b"err")
    container.exec_run.return_value = fake

    result = await backend.exec_in(ref, ["sh", "-c", "echo hi"])
    assert result.exit_code == 2
    assert result.stdout == b"out"
    assert result.stderr == b"err"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_wait_for_daemon_polls_until_running() -> None:
    backend, client = _build()
    ref = await backend.create(_spec())
    container = client.containers.get.return_value

    statuses = iter(
        [
            {"State": {"Status": "created"}, "Config": {"Image": "i"}},
            {"State": {"Status": "running"}, "Config": {"Image": "i"}},
        ]
    )

    def _reload() -> None:
        container.attrs = next(statuses)

    container.reload.side_effect = _reload
    backend = SysboxBackend(
        client=client,
        config=SysboxBackendConfig(daemon_healthcheck_timeout_s=2.0),
    )
    # First call to .reload() populates attrs to "created", then "running".
    await backend.wait_for_daemon(ref)


@pytest.mark.asyncio
async def test_wait_for_daemon_times_out() -> None:
    backend, client = _build()
    ref = await backend.create(_spec())
    container = client.containers.get.return_value
    container.attrs = {"State": {"Status": "created"}, "Config": {"Image": "i"}}
    backend = SysboxBackend(
        client=client,
        config=SysboxBackendConfig(daemon_healthcheck_timeout_s=0.05),
    )
    with pytest.raises(SandboxBackendError, match="did not enter running state"):
        await backend.wait_for_daemon(ref)


@pytest.mark.asyncio
async def test_create_failure_wraps_exception() -> None:
    backend, client = _build()
    client.containers.create.side_effect = RuntimeError("boom")
    with pytest.raises(SandboxBackendError, match=r"docker\.containers\.create failed"):
        await backend.create(_spec())


@pytest.mark.asyncio
async def test_get_container_missing_id_raises() -> None:
    backend, _client = _build()
    ref = SandboxRef(id="x", container_id=None, backend="sysbox")
    with pytest.raises(SandboxBackendError, match="no container_id"):
        await backend.start(ref)
