from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

POC_DIR = Path(__file__).resolve().parents[1]
RUNTIME_IMAGE = "gapt/runtime:dev"


def _run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    """Wrapper around subprocess.run that always captures text and never raises."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        **kw,  # type: ignore[arg-type]
    )


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    res = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return res.returncode == 0


def _sysbox_registered() -> bool:
    res = _run(["docker", "info", "--format", "{{json .Runtimes}}"])
    return "sysbox-runc" in res.stdout


def _runtime_image_exists() -> bool:
    res = _run(["docker", "image", "inspect", RUNTIME_IMAGE])
    return res.returncode == 0


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _docker_available():
        skip = pytest.mark.skip(reason="docker daemon unreachable")
        for item in items:
            item.add_marker(skip)
        return
    if not _sysbox_registered():
        skip = pytest.mark.skip(reason="sysbox-runc runtime not registered with docker")
        for item in items:
            item.add_marker(skip)
        return
    if not _runtime_image_exists():
        skip = pytest.mark.skip(
            reason=f"{RUNTIME_IMAGE} not built — run `bash boot_sysbox.sh` once first"
        )
        for item in items:
            item.add_marker(skip)


class Sandbox:
    """A freshly-booted Sysbox container the tests can poke at."""

    def __init__(self, name: str) -> None:
        self.name = name

    def exec(
        self,
        cmd: list[str],
        *,
        check: bool = False,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        full = ["docker", "exec", self.name, *cmd]
        res = _run(full, timeout=timeout)
        if check and res.returncode != 0:
            raise AssertionError(
                f"docker exec failed (rc={res.returncode}):\n"
                f"  cmd: {' '.join(cmd)}\n"
                f"  stdout: {res.stdout}\n"
                f"  stderr: {res.stderr}"
            )
        return res

    def shell(self, script: str, **kw: object) -> subprocess.CompletedProcess[str]:
        return self.exec(["bash", "-lc", script], **kw)  # type: ignore[arg-type]


def _spawn_sandbox(name: str, *, extra_args: list[str] | None = None) -> Sandbox:
    _run(["docker", "rm", "-f", name])  # idempotent
    args = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--runtime=sysbox-runc",
        "--device",
        "/dev/fuse:/dev/fuse",
        "--pids-limit",
        "256",
        "--memory",
        "512m",
        *(extra_args or []),
        RUNTIME_IMAGE,
        "/usr/local/bin/gapt-entrypoint",
        "sleep",
        "infinity",
    ]
    res = _run(args)
    if res.returncode != 0:
        raise AssertionError(f"docker run failed: {res.stderr}")

    sb = Sandbox(name)
    # Wait for inner dockerd; not strictly required for every test, but
    # makes failures localised — if it can't even start dockerd we'd
    # rather see one fixture error than nine confusing test errors.
    deadline = time.time() + 30
    while time.time() < deadline:
        if sb.exec(["docker", "info"]).returncode == 0:
            return sb
        time.sleep(1)
    raise AssertionError("inner dockerd never became ready")


def _destroy_sandbox(name: str) -> None:
    _run(["docker", "rm", "-f", name])


@pytest.fixture
def sandbox() -> Iterator[Sandbox]:
    name = f"gapt-iso-{uuid.uuid4().hex[:8]}"
    sb = _spawn_sandbox(name)
    try:
        yield sb
    finally:
        _destroy_sandbox(name)


@pytest.fixture
def sandbox_pair() -> Iterator[tuple[Sandbox, Sandbox]]:
    """For I8 — two independently-spawned sandboxes on separate networks."""
    n1, n2 = f"gapt-iso-{uuid.uuid4().hex[:8]}", f"gapt-iso-{uuid.uuid4().hex[:8]}"
    sb1 = _spawn_sandbox(n1)
    sb2 = _spawn_sandbox(n2)
    try:
        yield sb1, sb2
    finally:
        _destroy_sandbox(n1)
        _destroy_sandbox(n2)
