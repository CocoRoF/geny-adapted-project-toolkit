"""Unit + real-shell tests for the dev-service port reconcile
primitives (`domains/services/port_reconcile.py`).

These guard the EADDRINUSE-on-restart fix: a `next dev -p 3000` whose
previous run survived a GAPT restart inside the long-lived workspace
container, leaving :3000 held so the next start crashed. The registry
frees the port before spawning; this file pins the pure pieces it
relies on (port parsing, log-hint recovery, and the /proc kill script
that actually frees the port).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from gapt_server.domains.services.port_reconcile import (
    extract_log_port_hint,
    free_listener_pgid_script,
    parse_intended_port,
)


@pytest.mark.parametrize(
    ("cmd", "env", "declared", "expected"),
    [
        # No port anywhere — npm hides it in package.json.
        ("npm run dev", None, None, None),
        ("vite", None, None, None),
        # Explicit flags, both spacings.
        ("next dev --turbo -p 3000", None, None, 3000),
        ("next dev -p=3000", None, None, 3000),
        ("vite --port 5173", None, None, 5173),
        ("vite --port=5173 --host", None, None, 5173),
        ("uvicorn app:app --host 0.0.0.0 --port 8000", None, None, 8000),
        # Inline env assignment in the command.
        ("PORT=4000 npm run dev", None, None, 4000),
        # Positional forms.
        ("python -m http.server 8080", None, None, 8080),
        ("php -S 0.0.0.0:8000", None, None, 8000),
        # Effective $PORT: caller env wins over the declared start-form
        # port; both fall behind an explicit flag.
        ("npm run dev", {"PORT": "4321"}, None, 4321),
        ("npm run dev", None, 3333, 3333),
        ("next dev -p 3000", {"PORT": "9999"}, 7777, 3000),
        # Out-of-range / junk degrade to None rather than a bad port.
        ("serve --port 70000", None, None, None),
        ("npm run dev", {"PORT": "nope"}, None, None),
    ],
)
def test_parse_intended_port(
    cmd: str, env: dict[str, str] | None, declared: int | None, expected: int | None
) -> None:
    assert parse_intended_port(cmd, env, declared) == expected


def test_parse_ignores_lookalike_flags() -> None:
    """A flag that merely starts with the same letters as `--port` must
    not be read as one, and a sub-2-digit value isn't a dev port."""
    assert parse_intended_port("tool --portal 3000") is None
    assert parse_intended_port("git log -p 5") is None  # single digit
    assert parse_intended_port("next dev -p 3000") == 3000


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The exact bug-report log: echoed `-p 3000` + EADDRINUSE.
        (
            "> next dev --turbo -p 3000\n"
            "Error: listen EADDRINUSE: address already in use :::3000\n",
            3000,
        ),
        # EADDRINUSE alone (no echoed flag).
        ("Error: listen EADDRINUSE: address already in use :::8788\n", 8788),
        # Vite drift banner — take the live (last) port.
        ("Local: http://localhost:5173/\nPort 5173 in use\nLocal: http://localhost:5174/\n", 5174),
        ("ready - started server on 0.0.0.0:3000\n", 3000),
        ("", None),
        ("nothing useful here\n", None),
    ],
)
def test_extract_log_port_hint(text: str, expected: int | None) -> None:
    assert extract_log_port_hint(text) == expected


def test_free_script_is_dash_syntax_valid() -> None:
    """The reconcile script runs inside the container's /bin/sh (dash on
    the GAPT image). Confirm both signal variants parse under `sh -n`."""
    for sig in ("TERM", "KILL"):
        script = free_listener_pgid_script(3000, sig)
        r = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True, check=False)
        assert r.returncode == 0, f"sh -n rejected the {sig} script: {r.stderr}"
        # Bare `kill -<SIG> -<pgid>` (no `--`), the dash-accepted form.
        assert " -- -" not in script


def test_free_script_frees_a_real_listener() -> None:
    """Integration: spawn a child that LISTENs on an ephemeral port in
    its own session, run the generated TERM+KILL scripts, confirm the
    port is released and the child dies. Host stands in for the
    container — same Linux /proc + dash semantics.

    Targeting is by PORT (not a marker), and the port is one nobody
    else on the host holds, so the script cannot euthanise the test
    runner: pytest isn't listening on it. The child runs with
    start_new_session so the pgid kill targets only its group."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket,time;"
            "s=socket.socket();"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            f"s.bind(('127.0.0.1',{port}));s.listen(8);time.sleep(60)",
        ],
        start_new_session=True,
    )

    def listening() -> bool:
        out = subprocess.run(
            [
                "sh",
                "-c",
                f'awk \'$4=="0A"{{n=split($2,a,":"); if(a[n]=="{port:04X}") print}}\' '
                "/proc/net/tcp /proc/net/tcp6 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return bool(out.strip())

    try:
        # Wait for the child to actually bind.
        for _ in range(20):
            if listening():
                break
            time.sleep(0.1)
        assert listening(), "test setup: child never bound the port"

        subprocess.run(
            ["sh", "-c", free_listener_pgid_script(port, "TERM")],
            check=False,
            timeout=5,
            start_new_session=True,
        )
        subprocess.run(
            ["sh", "-c", free_listener_pgid_script(port, "KILL")],
            check=False,
            timeout=5,
            start_new_session=True,
        )

        for _ in range(30):
            if not listening():
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"port {port} still held after TERM+KILL scripts")

        assert child.wait(timeout=3) is not None
    finally:
        try:
            child.kill()
            child.wait(timeout=2)
        except Exception:
            pass
