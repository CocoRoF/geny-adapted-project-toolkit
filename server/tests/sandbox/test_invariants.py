"""Mount-whitelist invariants — these *must not* regress quietly.

Every entry asserts that a known-dangerous source path is rejected
*before* any backend gets to act on it.
"""

from __future__ import annotations

import pytest

from gapt_server.domains.sandbox.backend import (
    MountSpec,
    SecurityInvariantError,
    validate_mounts,
)


@pytest.mark.parametrize(
    "source",
    [
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/var/run",
        "/var/run/something",
        "/run",
        "/run/secrets/web",
        "/proc",
        "/proc/sys",
        "/sys",
        "/sys/fs/cgroup",
        "/dev/kmsg",
        "/root/.ssh",
        "/root/.ssh/id_rsa",
        "/home/alice/.ssh/known_hosts",
        "/tmp/ssh-agent.sock",
        "/home/alice/.aws",
        "/home/alice/.kube/config",
    ],
)
def test_forbidden_sources_rejected(source: str) -> None:
    with pytest.raises(SecurityInvariantError, match="refusing to bind-mount"):
        validate_mounts((MountSpec(source=source, target="/x"),))


@pytest.mark.parametrize(
    "source",
    [
        "/home/alice/projects/demo",
        "/srv/seaweedfs/volume-01",
        "/tmp/gapt-workspace",
        "/var/lib/gapt/data",
    ],
)
def test_safe_sources_allowed(source: str) -> None:
    # Should not raise.
    validate_mounts((MountSpec(source=source, target="/workspace"),))


def test_validate_mounts_short_circuits_on_first_violation() -> None:
    mounts = (
        MountSpec(source="/home/alice/projects/demo", target="/a"),
        MountSpec(source="/var/run/docker.sock", target="/b"),
        MountSpec(source="/srv/seaweed", target="/c"),
    )
    with pytest.raises(SecurityInvariantError, match=r"docker\.sock"):
        validate_mounts(mounts)
