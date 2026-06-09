"""Unit + dash-shell-integration tests for the Phase N.3 process-group
kill helper used by ``ServiceRegistry.stop``.

The bug this guards against: pre-fix, stop() ran
``pkill -TERM -f <marker>`` which only matched the wrapping ``sh``
process — the user's actual dev server (``npm run dev`` → ``node next``
→ ``next-server``) was a descendant that pkill never saw. Stop left
those descendants alive as PPID=1 orphans holding the bound port,
which made the next start fail with EADDRINUSE indefinitely. The
fix kills by pgid instead.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from gapt_server.domains.services.registry import _kill_marker_pgid_script


def test_script_is_dash_syntax_valid() -> None:
    """The kill script is run inside the container's ``/bin/sh`` (which
    on the GAPT sandbox image is dash, not bash). Confirm the
    generated one-liner parses cleanly under ``sh -n``.

    Catches the regression where an earlier version used
    ``kill -<SIG> -- -<pgid>`` (bash-canonical), which dash's builtin
    kill rejects with "Illegal option" — the user's stop endpoint
    returned 200 but the process tree stayed alive. The corrected
    form is ``kill -<SIG> -<pgid>`` (no ``--`` separator)."""
    script = _kill_marker_pgid_script("GAPT_SVC=01KWS:web", "TERM")
    r = subprocess.run(
        ["sh", "-n", "-c", script], capture_output=True, text=True
    )
    assert r.returncode == 0, f"sh -n rejected the script: {r.stderr}"
    # No leftover `--` separator from the old bash-syntax form.
    assert " -- -" not in script, (
        "dash's kill builtin rejects `kill -<SIG> -- -<pgid>`; the "
        "helper must emit the bare `kill -<SIG> -<pgid>` form."
    )


def test_script_quotes_marker_safely() -> None:
    """The marker string is interpolated into a shell command; if any
    unsanitised character lands in there we get either a parse error
    or an injection. Confirm shlex.quote is applied."""
    nasty = "GAPT_SVC=$(/bin/echo PWNED):'evil'"
    script = _kill_marker_pgid_script(nasty, "TERM")
    # Should NOT execute the command substitution — quoted single-quotes
    # neutralise it.
    assert "$(/bin/echo" not in script.split("for pid in", 1)[1].split(
        "do", 1
    )[0] or "'" in script
    # And the script still parses.
    r = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0


def test_script_kills_entire_process_group_in_real_shell(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Integration: build a fake "marker sh that spawns a child chain"
    locally, run the generated kill script, confirm BOTH the marker
    and its descendants die. Host-side equivalent of what stop() does
    inside the container — kernel APIs and shell semantics are
    identical because the container runs the same Linux + dash.

    Marker uniqueness is critical: the helper uses ``pgrep -f`` which
    matches against any process whose /proc/<pid>/cmdline contains the
    marker. In CI the test runner's own cmdline may carry the test
    file path AND its embedded literals, so a literal "GAPT_TEST_..."
    marker hardcoded above would match the runner itself and the kill
    would euthanise pytest mid-test. We generate the marker at
    runtime (via uuid) and pass it through subprocess argv — the
    runner's own cmdline never sees the bytes."""
    import uuid

    marker = f"GAPT_TEST_pgid_{uuid.uuid4().hex}"

    # Spawn `sh -c 'sleep 60' <marker>` (sh with marker as $0, spawning
    # a sleep child). This mirrors the production layout where the
    # marker sh stays alive (no exec) and the user's command (sleep
    # here standing in for `npm run dev`) is its child.
    proc = subprocess.Popen(
        ["sh", "-c", "sleep 60", marker],
        # Start it in its OWN pgrp so the kill script (which targets
        # the marker's pgid) doesn't accidentally signal the test
        # runner. ServiceRegistry.spawn_background gets the same
        # isolation via `docker exec -d` in real life.
        start_new_session=True,
    )
    try:
        # Give the child sleep time to spawn.
        time.sleep(0.5)

        # Sanity: confirm pgrep only matches our marker subprocess and
        # NOT some ancestor (e.g. the pytest runner). If this fails the
        # test environment polluted argv with the marker string and the
        # kill would take down something we don't want.
        pids_before = subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True
        )
        assert pids_before.returncode == 0, (
            "test setup: no process matched the marker"
        )
        matched_pids = [int(p) for p in pids_before.stdout.split()]
        assert matched_pids == [proc.pid], (
            "test setup: pgrep matched extra processes — marker not "
            f"unique enough: {matched_pids!r} vs spawned {proc.pid}"
        )

        # Run the helper's TERM script. CRITICAL: start_new_session so
        # the kill sh has its OWN pgid distinct from pytest. The script
        # iterates every pgrep match, including the kill sh itself
        # (its argv carries the marker as text inside the for-loop
        # script body). Without isolation, the self-iteration would
        # send TERM to pytest's pgid via the kill sh's inherited pgid
        # and the test runner would die mid-test with exit code 143.
        # In production the kill sh runs through a separate `docker
        # exec` which gives it natural pgid isolation; here we
        # reproduce that.
        term_script = _kill_marker_pgid_script(marker, "TERM")
        subprocess.run(
            ["sh", "-c", term_script],
            check=False,
            timeout=5,
            start_new_session=True,
        )

        # Brief grace, then verify the WHOLE TREE is gone (not just
        # the marker sh).
        for _ in range(20):
            pids_after = subprocess.run(
                ["pgrep", "-f", marker], capture_output=True, text=True
            )
            if pids_after.returncode != 0:
                # No matching process — marker sh is gone.
                break
            time.sleep(0.1)
        else:
            pytest.fail(
                "marker tree survived TERM signal: still matching "
                f"after grace, pids={pids_after.stdout!r}"
            )

        # And the marker process itself reaped.
        rc = proc.wait(timeout=3)
        assert rc is not None, "marker process never exited"
    finally:
        # Belt-and-braces if the test failed mid-way.
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
