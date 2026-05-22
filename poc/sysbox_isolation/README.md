# poc/sysbox_isolation/

> Plan: [`docs/plan/m0/p2_isolation_seaweedfs.md`](../../docs/plan/m0/p2_isolation_seaweedfs.md)
> Progress: [`docs/progress/m0/p2_isolation_seaweedfs.md`](../../docs/progress/m0/p2_isolation_seaweedfs.md)

Standalone proof of concept for the GAPT sandbox model: a `gapt/runtime:dev` container booted under `sysbox-runc` so that an *inner* dockerd can run safely DinD with no host docker socket exposure. This is the foundation that every later sandbox feature (per-project SeaweedFS mount, multi-workspace inner compose, the I1~I9 isolation matrix) is layered on.

## Prerequisites

- `sysbox-ce` installed (`/usr/bin/sysbox-runc` exists, `systemctl is-active sysbox` is `active`)
- `sysbox-runc` registered in `/etc/docker/daemon.json` and dockerd has reloaded
- The current user is in the `docker` group (or scripts are invoked under `sg docker -c "..."`)
- The repo root has `runtime/Dockerfile` available — `boot_sysbox.sh` builds `gapt/runtime:dev` on demand if it's not already in the local image cache

Run from anywhere — the boot script resolves the repo root via `git rev-parse`.

## Scripts

| Script | Purpose |
|---|---|
| `boot_sysbox.sh` | Build `gapt/runtime:dev` if missing, then `docker run --runtime=sysbox-runc` and wait up to 30s for inner dockerd to answer `docker info`. |
| `check_basic_isolation.sh` | First-pass isolation checks (B1~B5) against the booted container — host socket not mounted, inner Server Version distinct, runtime is `sysbox-runc`, inner `docker ps` clean, tool inventory present. |
| `teardown_sysbox.sh` | Stop + remove the container. Does not touch images. |
| `boot_seaweedfs.sh` | Stand up a standalone SeaweedFS instance on ports 19333/18888/18333 (shifted +10000 from the dev stack). |
| `check_seaweedfs.sh` | Functional checks (F1~F3): Filer HTTP PUT/GET/DELETE, persistence across container restart, S3 round-trip via boto3 (`uv run --with`). |
| `teardown_seaweedfs.sh` | Stop the SeaweedFS PoC; pass `--wipe` to drop volumes. |

| Decision doc | Purpose |
|---|---|
| `decision_volume_driver.md` | Why GAPT uses option B (container-side `weed mount` FUSE) instead of option A (SeaweedFS CSI volume plugin) for M0~M2. |
| `perf_seaweed_vs_host.md` | Destination for PR6 git-on-FUSE performance numbers (currently a placeholder). |

The full I1~I9 isolation matrix (the formal version of B1~B5) ships as automated pytest under `tests/` in PR5.

## Quick run

```bash
cd /path/to/geny-adapted-project-toolkit

# build + boot
sg docker -c "bash poc/sysbox_isolation/boot_sysbox.sh"

# verify
sg docker -c "bash poc/sysbox_isolation/check_basic_isolation.sh"

# clean up
sg docker -c "bash poc/sysbox_isolation/teardown_sysbox.sh"
```

`sg docker -c` is only needed if the calling shell doesn't already have the `docker` group active — i.e. on a freshly-added user before logout/login. New sessions resolve `docker` automatically and the wrapper can be dropped.

## What's verified by PR1 (this directory's first cut)

Run on 2026-05-22 against host:
- kernel 6.17.0-14-generic, cgroup v2
- docker-ce 29.2.1, sysbox-ce 0.6.7
- gapt/runtime:dev built off Ubuntu 24.04 (noble)

Results:

| Check | Outcome |
|---|---|
| Container boots under `sysbox-runc` | ✅ |
| Inner dockerd up within 8s (entrypoint waits to 30s) | ✅ (Server Version 29.5.2) |
| Host `/var/run/docker.sock` not mounted | ✅ (Mounts: `[]`) |
| Inner runtimes are `runc, io.containerd.runc.v2` only (no sysbox-runc) | ✅ (sysbox-runc is a host-side runtime) |
| Inner storage driver is `overlayfs` | ✅ (Sysbox uses overlayfs for nested overlay correctness) |
| Tools: git 2.43.0, gh 2.92.0, Python 3.12.3, Node 22.22.2, pnpm 11.2.2, uv 0.4.30, toolkit-agent 0.0.1, docker 29.5.2 | ✅ |

## What's verified by PR2 (this PoC's second cut)

Run on 2026-05-22 against the same host:

| Check | Outcome |
|---|---|
| Standalone SeaweedFS compose boots `healthy` on shifted ports | ✅ |
| F1 Filer HTTP PUT/GET/DELETE round-trip | ✅ |
| F2 S3 API via boto3 (create_bucket → put → get → list → delete) | ✅ |
| F3 Persistence across `docker restart` (file written, container restarted, file readable) | ✅ |
| Volume driver decision: option B (container-side `weed mount` FUSE) for M0~M2 | ✅ documented in [`decision_volume_driver.md`](decision_volume_driver.md) |

## What's *not* covered yet

These are intentionally deferred to later PRs in M0-P2:

- SeaweedFS Mount actually wired into `gapt/runtime` as `/workspace` (PR3 — applies the option-B sketch in `decision_volume_driver.md`)
- `git clone <external repo>` + `docker compose up` from within the inner dockerd, against a SeaweedFS-mounted workspace (PR4)
- The full I1~I9 escape-attempt matrix from `docs/06_isolation_and_runtime.md` §6.10 (PR5)
- SeaweedFS-vs-host-FS git performance numbers — fills `perf_seaweed_vs_host.md` (PR6)
- A CI workflow that runs the matrix on a self-hosted runner (PR6 or follow-up)

## Image notes

`gapt/runtime:dev` is built from `runtime/Dockerfile` (multi-stage, Ubuntu 24.04 base). The `--system` pip install path that worked on Debian bookworm fails under PEP 668 on noble, so the daemon now lives in a self-contained venv at `/opt/gapt-runtime-venv` and `/usr/local/bin/toolkit-agent` is a symlink into that venv.
