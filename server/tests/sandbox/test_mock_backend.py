"""MockSandboxBackend — state-machine smoke + invariants enforced at
the protocol boundary."""

from __future__ import annotations

import pytest

from gapt_server.domains.sandbox import (
    ExecResult,
    MockSandboxBackend,
    MountSpec,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxResources,
    SecurityInvariantError,
)


def _spec(*, mounts: tuple[MountSpec, ...] = ()) -> SandboxCreateSpec:
    return SandboxCreateSpec(
        project_id="01KS900000000000000000PRJX",
        workspace_id=None,
        image="ghcr.io/cocorof/gapt-runtime:dev",
        resources=SandboxResources(),
        mounts=mounts,
    )


@pytest.mark.asyncio
async def test_full_state_machine() -> None:
    backend = MockSandboxBackend()
    ref = await backend.create(_spec())

    info = await backend.inspect(ref)
    assert info.status == "creating"
    assert info.image.endswith("gapt-runtime:dev")

    await backend.start(ref)
    assert (await backend.inspect(ref)).status == "running"

    result = await backend.exec_in(ref, ["echo", "hi"])
    assert result.exit_code == 0
    assert backend.exec_log(ref)[0] == ["echo", "hi"]

    await backend.stop(ref)
    assert (await backend.inspect(ref)).status == "stopped"

    # Cannot exec on a stopped sandbox.
    with pytest.raises(SandboxBackendError, match="cannot exec"):
        await backend.exec_in(ref, ["echo", "no"])

    await backend.destroy(ref)
    with pytest.raises(SandboxBackendError, match="unknown sandbox"):
        await backend.inspect(ref)


@pytest.mark.asyncio
async def test_create_rejects_docker_socket_mount() -> None:
    backend = MockSandboxBackend()
    spec = _spec(mounts=(MountSpec(source="/var/run/docker.sock", target="/x"),))
    with pytest.raises(SecurityInvariantError):
        await backend.create(spec)


@pytest.mark.asyncio
async def test_canned_exec_response() -> None:
    backend = MockSandboxBackend()
    backend.set_exec_response(
        "git",
        ExecResult(exit_code=0, stdout=b"abc123\n", stderr=b"", duration_ms=12),
    )
    ref = await backend.create(_spec())
    await backend.start(ref)
    result = await backend.exec_in(ref, ["git", "rev-parse", "HEAD"])
    assert result.stdout == b"abc123\n"
    assert result.duration_ms == 12


@pytest.mark.asyncio
async def test_destroy_then_start_is_rejected() -> None:
    backend = MockSandboxBackend()
    ref = await backend.create(_spec())
    await backend.destroy(ref)
    with pytest.raises(SandboxBackendError, match="unknown sandbox"):
        await backend.start(ref)
