"""Phase E.1 — GPU passthrough into `gapt-ws-<wid>` containers.

Two surfaces under test:

1. ``format_gpus_flag()`` — the spec → docker-arg translator. Pure
   function; verifies it accepts the documented forms and rejects
   garbage so we never emit a malformed ``--gpus`` flag.

2. ``WorkspaceSandbox.ensure()`` — verify the argv assembly includes
   ``--gpus <value>`` only when the spec is set. We intercept the
   internal ``_run_docker`` so the test doesn't need a real docker
   daemon AND so we can capture the final argv.
"""

from __future__ import annotations

import pytest

from gapt_server.domains.workspace_sandbox import manager as ws_mod
from gapt_server.domains.workspace_sandbox.manager import (
    WorkspaceSandbox,
    WorkspaceSandboxManager,
    format_gpus_flag,
)


# ─────────────────────────────────────── format_gpus_flag ──


def test_format_gpus_none_returns_none() -> None:
    assert format_gpus_flag(None) is None


def test_format_gpus_empty_string_returns_none() -> None:
    """Treat empty/whitespace exactly like None — keeps CPU-only
    hosts working when an env var is exported empty by mistake."""
    assert format_gpus_flag("") is None
    assert format_gpus_flag("   ") is None


def test_format_gpus_all_normalises_case() -> None:
    assert format_gpus_flag("all") == "all"
    assert format_gpus_flag("ALL") == "all"
    assert format_gpus_flag(" All ") == "all"


def test_format_gpus_single_index() -> None:
    assert format_gpus_flag("0") == "device=0"
    assert format_gpus_flag("3") == "device=3"


def test_format_gpus_multi_index_csv() -> None:
    assert format_gpus_flag("0,1") == "device=0,1"
    assert format_gpus_flag("0,1,2,3") == "device=0,1,2,3"


def test_format_gpus_rejects_garbage() -> None:
    """Anything that isn't `all` or a digit-CSV becomes None so the
    docker argv stays clean. Operators see "no GPU" rather than a
    surprising failure."""
    for bad in ("none", "auto", "0,a", "0,1,", ",", "1.5", "all,0", "0 1"):
        assert format_gpus_flag(bad) is None, bad


# ───────────────────────────────────── WorkspaceSandbox argv ──


async def _capture_argv(
    monkeypatch: pytest.MonkeyPatch, sandbox: WorkspaceSandbox
) -> list[str]:
    """Run ensure() with a stubbed _run_docker that records the docker
    run argv. Returns the captured tail (everything after the
    initial inspect probe)."""
    calls: list[list[str]] = []

    async def fake_run_docker(*args: str, timeout_s: float = 30.0) -> tuple[int, str, str]:
        calls.append(list(args))
        # `inspect` of the container name (first call inside ensure)
        # — pretend the container is missing so the path falls
        # through to `docker run`.
        if args[:2] == ("inspect", "-f"):
            return (1, "", "No such object")
        # `network inspect` short-circuit — pretend exists.
        if args[:2] == ("network", "inspect"):
            return (0, "", "")
        # The actual `docker run` — succeeds.
        if args[:1] == ("run",):
            return (0, "container-id\n", "")
        return (0, "", "")

    monkeypatch.setattr(ws_mod, "_run_docker", fake_run_docker)

    await sandbox.ensure()
    # Find the `docker run` call.
    run_calls = [c for c in calls if c and c[0] == "run"]
    assert len(run_calls) == 1, calls
    return run_calls[0]


@pytest.mark.asyncio
async def test_argv_omits_gpus_when_unset(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy / CPU-only path — `gpus=None` must produce argv
    without any `--gpus` flag. Regression guard against an
    accidental always-on default."""
    sandbox = WorkspaceSandbox(
        workspace_id="01KSWS00000000000000000000",
        worktree_path=str(tmp_path),
        image="gapt-workspace:latest",
        container_name="gapt-ws-01ksws00000000000000000000",
        gpus=None,
    )
    argv = await _capture_argv(monkeypatch, sandbox)
    assert "--gpus" not in argv


@pytest.mark.asyncio
async def test_argv_includes_gpus_all(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = WorkspaceSandbox(
        workspace_id="01KSWS00000000000000000001",
        worktree_path=str(tmp_path),
        image="gapt-workspace:latest",
        container_name="gapt-ws-01ksws00000000000000000001",
        gpus="all",
    )
    argv = await _capture_argv(monkeypatch, sandbox)
    # The flag immediately precedes its value in our argv builder.
    idx = argv.index("--gpus")
    assert argv[idx + 1] == "all"


@pytest.mark.asyncio
async def test_argv_includes_gpus_device_csv(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = WorkspaceSandbox(
        workspace_id="01KSWS00000000000000000002",
        worktree_path=str(tmp_path),
        image="gapt-workspace:latest",
        container_name="gapt-ws-01ksws00000000000000000002",
        gpus="0,1",
    )
    argv = await _capture_argv(monkeypatch, sandbox)
    idx = argv.index("--gpus")
    assert argv[idx + 1] == "device=0,1"


@pytest.mark.asyncio
async def test_argv_omits_gpus_for_garbage_spec(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed spec from a typo in env var must result in no
    `--gpus` flag — never a half-formed one that docker rejects with
    cryptic errors."""
    sandbox = WorkspaceSandbox(
        workspace_id="01KSWS00000000000000000003",
        worktree_path=str(tmp_path),
        image="gapt-workspace:latest",
        container_name="gapt-ws-01ksws00000000000000000003",
        gpus="auto",  # not valid
    )
    argv = await _capture_argv(monkeypatch, sandbox)
    assert "--gpus" not in argv


# ──────────────────────────────────── manager → sandbox plumbing ──


def test_manager_stamps_gpus_on_new_handle(tmp_path) -> None:
    """Setting-level GPU policy reaches every WorkspaceSandbox
    handle the manager creates."""
    mgr = WorkspaceSandboxManager(gpus="all")
    sandbox = mgr.get("01KSWS00000000000000000010", str(tmp_path))
    assert sandbox.gpus == "all"


def test_manager_get_existing_handle_picks_up_setting_change(tmp_path) -> None:
    """Operator flips the global setting mid-flight. The next get()
    call on a known workspace must reflect the change so the next
    ensure() boot uses the new GPU policy. Existing live containers
    keep their old mapping until restarted (out of scope here)."""
    mgr = WorkspaceSandboxManager(gpus=None)
    sandbox = mgr.get("01KSWS00000000000000000011", str(tmp_path))
    assert sandbox.gpus is None

    mgr.gpus = "all"  # operator changed the global setting
    again = mgr.get("01KSWS00000000000000000011", str(tmp_path))
    assert again is sandbox  # same handle
    assert again.gpus == "all"
