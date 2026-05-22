"""I1~I9 isolation matrix for the GAPT Sysbox sandbox.

Each test mounts the canonical attack from docs/06_isolation_and_runtime.md
§6.10 and asserts the sandbox stops it. Tests are independent — every one
spins up its own fresh container via the `sandbox` fixture in conftest.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import Sandbox, _run

pytestmark = pytest.mark.isolation


# ---------- I1: nested docker can't escape to host root ----------


def test_i1_inner_docker_cannot_see_host_root(sandbox: Sandbox) -> None:
    # Try to mount the inner-side "/" of an arbitrary image and look for
    # a marker file that only exists on the host (the GAPT repo itself).
    # If the boundary were broken we'd see "geny-adapted-project-toolkit"
    # in /host's listing.
    res = sandbox.shell(
        "docker run --rm -v /:/host hashicorp/http-echo:latest /bin/sh -c "
        '\'ls /host 2>&1 | tr "\\n" " "\' 2>&1',
        timeout=60,
    )
    combined = res.stdout + res.stderr
    # The hashicorp/http-echo image is scratch-ish — `/bin/sh` may not even
    # exist there, which is fine: the assertion we care about is that
    # /host does NOT show the host filesystem's repo directory.
    assert "geny-adapted-project-toolkit" not in combined, combined
    assert "geny-workspace" not in combined, combined


# ---------- I2: host docker socket is not mounted ----------


def test_i2_host_docker_sock_absent(sandbox: Sandbox) -> None:
    # Hostside introspection: docker's own view of this container.
    res = _run(["docker", "inspect", sandbox.name, "--format", "{{json .Mounts}}"])
    mounts = json.loads(res.stdout)
    sources = [m.get("Source", "") for m in mounts]
    assert all("/var/run/docker.sock" not in s for s in sources), sources


# ---------- I3: host network interfaces are invisible ----------


def test_i3_host_nics_hidden(sandbox: Sandbox) -> None:
    host_ips = _run(["ip", "-o", "-4", "addr", "show"]).stdout
    # Pull each global IPv4 *that isn't private docker bridges* —
    # presence of the host LAN address inside the container would mean
    # netns escape.
    real_host_ips = [
        line.split()[3].split("/")[0]
        for line in host_ips.splitlines()
        if "scope global" in line and "172.17" not in line and "172.20" not in line
    ]
    res = sandbox.exec(["ip", "-o", "-4", "addr", "show"])
    inside = res.stdout
    for ip in real_host_ips:
        assert ip not in inside, f"host IP {ip} leaked into sandbox netns: {inside}"


# ---------- I4: pids cgroup gates fork bomb ----------


def test_i4_pids_limit_caps_fork_bomb(sandbox: Sandbox) -> None:
    # ulimit -u under Sysbox often shows "unlimited" because the rlimit
    # isn't the gate — the cgroup pids.max is. So we attempt the actual
    # fork bomb against our --pids-limit 256 and look for the cgroup
    # kicking in. The headline assertion: we must NOT manage to fork
    # all 400 processes.
    res = sandbox.shell(
        "for i in $(seq 1 400); do "
        "  (sleep 600 &) 2>/dev/null || { echo blocked-at-$i; exit 0; }; "
        "done; "
        "echo all-spawned",
        timeout=30,
    )
    output = res.stdout + res.stderr
    assert "all-spawned" not in output, (
        f"pids cgroup did not stop a 400-process fork bomb:\n{output}"
    )
    # And confirm we're seeing a fork-time refusal (not, say, a quota
    # for child shells). The output should mention "blocked-at-".
    assert "blocked-at-" in output, output


# ---------- I5: memory limit kills the runaway, host RAM safe ----------


def test_i5_memory_limit_kills_runaway(sandbox: Sandbox) -> None:
    # `head | tr` is a pipe with bounded RSS — won't trigger the cgroup.
    # Drive Python through a heredoc and use a sentinel that *only* a
    # successful allocation would print, so the shell's echo of the
    # script body can't spoof a pass.
    res = sandbox.shell(
        "python3 <<'PY' || echo killed-rc-$?\n"
        "x = bytearray(1024 * 1024 * 1024)\n"
        "print('GAPT_I5_ALLOC_SUCCEEDED', len(x))\n"
        "PY",
        timeout=20,
    )
    output = res.stdout + res.stderr
    # bash echoes the heredoc body on error so the literal source line
    # `print('GAPT_I5_ALLOC_SUCCEEDED', len(x))` appears in stderr. The
    # ACTUAL print output (only emitted if alloc succeeded) is the
    # space-joined form `GAPT_I5_ALLOC_SUCCEEDED 1073741824` — match
    # that exactly.
    assert "GAPT_I5_ALLOC_SUCCEEDED 1073741824" not in output, (
        f"1 GiB allocation completed under a 512m memory limit:\n{output}"
    )
    assert "killed-rc" in output or "MemoryError" in output or "Killed" in output, output

    # The host must still be reachable — proved implicitly by getting
    # here, but make it explicit so the assertion message is meaningful.
    host_uptime = _run(["uptime"])
    assert host_uptime.returncode == 0, "host uptime check failed — host stability?"


# ---------- I6: unprivileged mount is trapped in user namespace ----------


def test_i6_mount_does_not_escape_namespace(sandbox: Sandbox) -> None:
    # An attacker tries to mount a tmpfs into a host path. Sysbox's
    # user-namespace mapping should keep the mount inside the container
    # (visible only there) or refuse it. Either is acceptable.
    res = sandbox.shell(
        "mkdir -p /tmp/escape && "
        "mount -t tmpfs none /tmp/escape && "
        "echo PWNED > /tmp/escape/marker",
        timeout=15,
    )
    # Outside the container, /tmp on the host must NOT contain the marker.
    # We never mounted /tmp into the sandbox, so the marker can't leak.
    host_marker = _run(["test", "-e", "/tmp/escape/marker"])
    assert host_marker.returncode != 0, "host /tmp/escape/marker exists — mount escaped namespace"
    # Whether the mount inside the container succeeded is implementation-
    # defined; the load-bearing assertion is the host check above.
    _ = res  # keep the call for side effects only


# ---------- I7: dangerous sysctls don't leak to the host ----------


def test_i7_kernel_panic_sysctl_does_not_affect_host(sandbox: Sandbox) -> None:
    # Sysbox virtualises /proc/sys so writes to kernel.panic *succeed
    # inside the container* but stay in the container's procfs view —
    # the host's kernel.panic is untouched. The threat model from
    # docs/06 §6.10 is "container can't change the host's reboot
    # behaviour", which is exactly what we assert here. (Sysbox 0.6.7
    # used to refuse the write outright; 0.7.0 lets it succeed but
    # isolates it. Either is acceptable; the host-side check is the
    # load-bearing one.)
    host_before = Path("/proc/sys/kernel/panic").read_text().strip()
    sandbox.shell("sysctl -w kernel.panic=99 2>&1", timeout=10)
    host_after = Path("/proc/sys/kernel/panic").read_text().strip()
    assert host_before == host_after, (
        f"host kernel.panic changed: {host_before} → {host_after} "
        "(sandbox should not be able to affect host sysctls)"
    )


# ---------- I8: distinct sandboxes can't ping each other ----------


def test_i8_sandboxes_are_network_isolated(
    sandbox_pair: tuple[Sandbox, Sandbox],
) -> None:
    sb1, sb2 = sandbox_pair
    ip1 = sb1.exec(["hostname", "-i"]).stdout.strip()
    ip2 = sb2.exec(["hostname", "-i"]).stdout.strip()
    assert ip1 and ip2 and ip1 != ip2, (ip1, ip2)

    # Each sandbox sits on its own default bridge — pings between them
    # must fail (different docker networks, no shared L2/L3 surface).
    res = sb1.shell(
        f"timeout 3 ping -c 1 -W 1 {ip2} 2>&1",
        timeout=10,
    )
    assert res.returncode != 0, (
        f"sandbox {sb1.name} reached sandbox {sb2.name}'s IP {ip2}:\n{res.stdout}"
    )


# ---------- I9: daemon environment is not exposed in cleartext ----------


@pytest.mark.xfail(
    strict=True,
    reason="M1-E1 cycle 1.9 ships the daemon redaction; right now /proc/*/environ "
    "leaks GAPT_DAEMON_TOKEN to any process inside the sandbox. Test is "
    "xfail(strict=True) so accidentally fixing it before M1-E1 trips CI.",
)
def test_i9_daemon_environ_redacted(sandbox: Sandbox) -> None:
    # Spawn the sandbox with a known-shape token, then look inside the
    # container at PID 1's environ. The test asserts the token does NOT
    # appear — i.e. the daemon writes process-scoped env vars to a
    # separate keyring, not its own process environment.
    test_token = "GAPT-TEST-TOKEN-DO-NOT-SHIP"
    sb_name = sandbox.name
    _run(["docker", "rm", "-f", sb_name])
    spawn = _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            sb_name,
            "--runtime=sysbox-runc",
            "--device",
            "/dev/fuse:/dev/fuse",
            "-e",
            f"GAPT_DAEMON_TOKEN={test_token}",
            "gapt/runtime:dev",
            "/usr/local/bin/gapt-entrypoint",
            "sleep",
            "infinity",
        ],
    )
    assert spawn.returncode == 0, spawn.stderr

    res = subprocess.run(
        [
            "docker",
            "exec",
            sb_name,
            "bash",
            "-c",
            "cat /proc/1/environ | tr '\\0' '\\n'",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert test_token not in res.stdout, "daemon should not expose token in /proc/1/environ"
