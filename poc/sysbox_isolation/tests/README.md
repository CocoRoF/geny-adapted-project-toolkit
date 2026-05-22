# poc/sysbox_isolation/tests/

Automated `I1`~`I9` sandbox isolation matrix from [`docs/06_isolation_and_runtime.md`](../../../docs/06_isolation_and_runtime.md) §6.10. Each numbered test is one of the host-escape attempts the GAPT sandbox model has to defeat; the file `test_isolation.py` runs all nine against a freshly-booted Sysbox sandbox.

## Prereqs

- sysbox-ce **>= 0.7.0** registered with docker (`docker info | grep sysbox`)
- `/dev/fuse` exposed on the host (true on any standard Linux 5.10+)
- `gapt/runtime:dev` image present (or buildable from the repo root)
- `uv` on the host PATH

## Run

```bash
cd poc/sysbox_isolation/tests
uv sync --extra dev
sg docker -c "uv run pytest -v"
```

Each test boots a *fresh* Sysbox sandbox via `boot_sysbox.sh`, runs its single escape attempt, and tears the sandbox down. Wall-clock per case is 5~15s; the whole matrix completes in ≤ 2 minutes.

## What I1~I9 verify

| Test | Attempts | Should | Source |
|---|---|---|---|
| I1 | inner `docker run -v /:/host alpine ls /host` | only see inner `/`, not host `/` | T6 / [06 §6.10](../../../docs/06_isolation_and_runtime.md) |
| I2 | look for the host's `/var/run/docker.sock` inside the sandbox | not present | T6 |
| I3 | `ip addr` from inside the sandbox | host interfaces invisible (no host MAC) | [06 §6.5](../../../docs/06_isolation_and_runtime.md) |
| I4 | fork bomb inside the sandbox | killed by `pids` cgroup; host stays healthy | [06 §6.4](../../../docs/06_isolation_and_runtime.md) |
| I5 | huge memory allocation inside the sandbox | OOM-killed inside the container; host RAM safe | [06 §6.4](../../../docs/06_isolation_and_runtime.md) |
| I6 | `mount -t tmpfs ...` inside the sandbox without user-namespace mapping | EPERM or namespace-trapped | [06 §6.10](../../../docs/06_isolation_and_runtime.md) |
| I7 | `sysctl kernel.panic=1` inside the sandbox | EPERM | [06 §6.10](../../../docs/06_isolation_and_runtime.md) |
| I8 | a second sandbox tries to ping the first | unreachable (separate docker networks) | [06 §6.5](../../../docs/06_isolation_and_runtime.md) |
| I9 | look at `/proc/<daemon-pid>/environ` for raw secrets | tokens are not exposed in cleartext to non-privileged readers in the container | [09 §9.3.4](../../../docs/09_security_authz_observability.md) |

I9 is currently a **regression bar** rather than a pass — the daemon doesn't yet redact env vars from its own `/proc` view (M1-E1 cycle 1.9 ships the daemon proper). The test is marked `xfail(strict=True)` so that *if* we accidentally fix it before M1-E1, CI tells us to revisit.

## Where to find the failures

```bash
uv run pytest -v --capture=no
uv run pytest -v -k I4               # just one test
uv run pytest --collect-only         # see the test list
```

Failures dump the offending command's stdout + stderr in the assertion message.
