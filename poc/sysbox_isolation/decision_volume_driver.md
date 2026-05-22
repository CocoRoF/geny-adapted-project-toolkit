# Decision: How GAPT sandbox containers mount SeaweedFS as `/workspace`

> Plan: [`../../docs/plan/m0/p2_isolation_seaweedfs.md`](../../docs/plan/m0/p2_isolation_seaweedfs.md) §2 (SeaweedFS volume driver 선택)
> Status: **decided** — go with **(B) container-side FUSE mount** for M0~M2; revisit (A) at M4+.

GAPT's invariant ([06 §6.3.2](../../docs/06_isolation_and_runtime.md)) is that every per-project workspace's filesystem is SeaweedFS-backed, never a raw host bind. Two ways for the Sysbox sandbox container to actually see a SeaweedFS path at `/workspace`:

## Option A — Docker volume plugin (`seaweedfs/seaweedfs-csi-driver`)

The community ships [`seaweedfs/seaweedfs-csi-driver`](https://github.com/seaweedfs/seaweedfs-csi-driver) as a CSI plugin. Once it's installed and registered with the host's CSI-enabled orchestrator, you can declare a `volume` whose `driver: weed` resolves to a SeaweedFS path.

**Pros**
- Volume lifecycle (create/mount/unmount) is owned by docker/containerd, not the sandbox image.
- Same volume declaration syntax that compose users already know (`driver: weed`).
- Maps naturally to Kubernetes when M4 lands — the same CSI driver runs in-cluster.

**Cons (today)**
- CSI is a *Kubernetes-first* contract. Outside K8s the integration is partial: docker engine talks to docker volume plugins via the legacy plugin API, not CSI. The published driver is CSI-only — there's no "vanilla docker volume plugin" for SeaweedFS upstream.
- The bridge that would make `docker volume create --driver weed` work needs an extra adapter (`docker-csi-plugin` or `rancher-volume-plugin`-style shim), which adds a third moving part and a separate ports/config surface to break.
- The plugin runs as a privileged process on the host. That violates the spirit of "the host should know as little as possible about workspace internals."
- Plugin install + uninstall is *global to the docker daemon*. A broken upgrade affects every container on the host, not just GAPT.

**Maturity**
- The driver is alive but the docker (non-K8s) story is "rough edges and write your own glue" as of 2026. We'd be early.

## Option B — Container-side FUSE mount (`weed mount`)

`gapt/runtime`'s entrypoint runs `weed mount -filer=<filer-url> -dir=/workspace` against the host-side SeaweedFS filer URL passed in via env, before exec'ing the daemon. The kernel sees a regular FUSE mount inside the sandbox; everything above it (git, the inner dockerd's data dir option, the user's code) is unchanged.

**Pros**
- One image, one entrypoint, one external dependency (a filer URL + S3 creds in env). No host-level plugin install.
- Sysbox supports unprivileged FUSE inside the sandbox out of the box — no extra capability concessions.
- Failure surface is local: a broken FUSE mount only affects that one sandbox.
- Works identically on docker, K8s, podman.
- Easy to swap out — replacing SeaweedFS with another S3-compatible filer means changing one env var.

**Cons**
- Performance overhead. `weed mount` is FUSE, with per-syscall context switches. For workloads that hit metadata heavily (git status on a large tree, npm install) this is *meaningfully* slower than a local overlay. PR6 measures the actual numbers and decides per-directory placement.
- Mount lives only as long as the container — restart shows a brief window where `/workspace` is missing while `weed mount` reconnects.
- All sandboxes share the same SeaweedFS instance; one user's bad behaviour on `weed mount` flags can affect everyone (mitigated by per-sandbox unique mount roots).

**Maturity**
- `weed mount` is the SeaweedFS team's primary path for "treat S3 like a filesystem" and ships in every release. PoC verified in this PR: 3/3 functional checks pass (Filer PUT/GET/DELETE, S3 round-trip, persistence across container restart).

## Decision

**Use option B (container-side `weed mount`) through M0~M2**, and re-evaluate option A at the M4 multi-node / Kubernetes step.

Rationale:
1. M0~M2 are *single-node* by design. Option A's main advantage (orchestrator-managed lifecycle) costs us nothing valuable at that scale.
2. We want the smallest blast radius per mount failure. FUSE inside the sandbox keeps blast radius to one container.
3. Sysbox + FUSE is already tested upstream; the Sysbox CSI angle is not.
4. Performance — the deciding "is this even tolerable" data — is collected in PR6 (`docs/plan/m0/p2_isolation_seaweedfs.md` §7) and a hybrid layout (e.g., `.git` on a local named volume, working tree on FUSE) is the documented escape hatch if numbers come back bad.
5. At M4 the CSI driver becomes natural alongside K8s, so option A returns to the table then. The `SandboxBackend` adapter ([03 §3.6](../../docs/03_system_architecture.md)) keeps both alive behind one interface.

## Wiring sketch (option B)

In `runtime/Dockerfile`, install the SeaweedFS `weed` client binary (~30 MB), then in `gapt-entrypoint`:

```bash
if [[ -n "${GAPT_SEAWEED_FILER_URL:-}" ]]; then
    mkdir -p /workspace
    weed mount \
        -filer="$GAPT_SEAWEED_FILER_URL" \
        -dir=/workspace \
        -filer.path="${GAPT_SEAWEED_PATH:-/}/projects/${GAPT_PROJECT_ID}/workspaces/${GAPT_WORKSPACE_ID}" \
        &
    # Wait for the mount to appear.
    for _ in $(seq 1 20); do
        mountpoint -q /workspace && break
        sleep 0.5
    done
fi
```

PR3 in this milestone wires this in and proves git clone + tree readback work over it.

## Settled by PR6 numbers (see [`perf_seaweed_vs_host.md`](perf_seaweed_vs_host.md))

- **Hybrid `.git`-on-host / worktree-on-FUSE: deferred.** PR6's measurements on `pre-commit/pre-commit` show 2.5~5x slowdown on read-only ops (single-digit ms — imperceptible) and 24x on `git checkout` (437 ms vs 18 ms — sub-second, noticeable but not flow-breaking). The hybrid layout's complexity cost outweighs the win at M0~M2 scale. Revisit at M2 with real dogfood checkout cadence, and again at M4 when SeaweedFS goes multi-node.
- **Inner dockerd's overlay2 storage stays on a host-side named volume**, not SeaweedFS. Overlay2 over FUSE has known correctness issues and the project memory ("영속 파일은 무조건 SeaweedFS") carves out caches as an explicit exception. The wiring sketch above stays as-is.

## Still open (deferred to later cycles)

- Per-volume quotas via SeaweedFS Filer's directory ACLs — that's an M1-E1 cycle 1.11 problem.
- Multi-node SeaweedFS replication and the option-A CSI driver reconsideration — M4 work.
