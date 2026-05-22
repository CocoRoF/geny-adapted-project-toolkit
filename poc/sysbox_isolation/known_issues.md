# Known issues encountered while building M0-P2

> Plan: [`../../docs/plan/m0/p2_isolation_seaweedfs.md`](../../docs/plan/m0/p2_isolation_seaweedfs.md)
> Progress: [`../../docs/progress/m0/p2_isolation_seaweedfs.md`](../../docs/progress/m0/p2_isolation_seaweedfs.md)

This is the bin for upstream / environment-level snags that surfaced during PoC work and *won't* be resolved inside this PoC. They're catalogued here so M1-E1's Sandbox Controller (which inherits everything in this directory) knows exactly what to design around.

---

## KI-1. docker 25+ `net.ipv4.ip_unprivileged_port_start` vs Sysbox 0.6.7 virtualised procfs (✅ resolved by Sysbox 0.7.0)

**Resolution (2026-05-23):** Upgrading sysbox-ce from 0.6.7 → 0.7.0 (the binary published at `https://downloads.nestybox.com/sysbox/releases/v0.7.0/sysbox-ce_0.7.0-0.linux_amd64.deb`, ahead of the GitHub-releases listing) makes the cross-device `openat2` write succeed. Verified on this host (kernel 6.17, docker-ce 29.2.1) — `check_inner_compose.sh` C0~C4 all PASS, including inner `docker compose up` with `hashicorp/http-echo` reachable on the inner network. The original sysbox-fs commit that fixes the path is [`1302a6f`](https://github.com/nestybox/sysbox-fs/commit/1302a6f3fbe04f9c10488fe8c4195a504980cf6f) per the comment thread on nestybox/sysbox#973.

The historical diagnosis is kept below for reference; any later host that ends up on an older Sysbox needs the upgrade.

---

**Surface:** every `docker run` / `docker compose up` issued by the *inner* dockerd fails container init with:

```
runc create failed: unable to start container process:
  error during container init:
  open sysctl net.ipv4.ip_unprivileged_port_start file:
  unsafe procfs detected:
  openat2 /proc/./sys/net/ipv4/ip_unprivileged_port_start: invalid cross-device link
```

This affects even `docker run hello-world` — it is *not* a port-binding problem, not a compose problem, not a privileged-port problem. It happens before the inner container has run a single instruction of user code.

**Root cause:** docker engine 25.0 (`moby/moby#47645`) made the engine *unconditionally write* `net.ipv4.ip_unprivileged_port_start` into every new container's procfs, so callers can opt into binding ports < 1024 without `CAP_NET_BIND_SERVICE`. Sysbox 0.6.7's `sysbox-fs` virtualises `/proc/sys` paths but the implementation marks that particular file as cross-device against the container's mount namespace; the kernel's `openat2(RESOLVE_NO_XDEV)` check then rejects the write inside `runc init`.

**What we tried (none of which work today):**
- `--storage-driver=vfs` (rules out an overlay-fs issue — same crash)
- `--security-opt systempaths=unconfined` on the inner service (compose-level — still hit by runc init)
- explicit `--sysctl net.ipv4.ip_unprivileged_port_start=1024` (engine ignores the override and re-emits the file)

**What's verified to still work despite this:**
- Inner dockerd boots, exposes its own `/var/run/docker.sock`, reports `Server Version 29.5.2` (distinct from the host's 29.2.1).
- `docker pull` against the inner registry layer completes — the image is fully extracted into the inner storage.
- `docker create` succeeds (the container record exists in the inner daemon).
- Only `docker start` / `docker run --rm` fails, at runc init.
- The host's docker is *not* aware of any inner container — meaning the boundary itself holds; nothing leaks even when the inner side is broken.

**Resolution candidates (deferred to a follow-up cycle):**

1. **Pin inner docker-ce to 24.x** in `runtime/Dockerfile`. docker 24 predates the auto-sysctl behaviour. Cost: outdated runtime relative to host, must track Docker 24 → 25 deprecation.
2. **Wait for / drive Sysbox release** that handles the cross-device write — `sysbox-fs` would need to mark that path as virtualised-and-writable. There's no GitHub issue filed yet — *we should open one* with the trace above.
3. **Build a custom sysbox-fs / OCI hook** that intercepts the offending `openat2` call. Realistically out of scope until M4+.
4. **Adopt Docker Hardened Desktop's ECI runtime** if/when it's available outside Desktop. It bundles sysbox + a docker engine known to coexist.

**Impact on the GAPT roadmap:**
- M0-P2 PR4 ("inner dockerd로 외부 repo compose up 검증") is *partially* met — the dockerd half works, the container-launch half doesn't. The plan card's DoD bullet "Sysbox 컨테이너 안에서 `git clone <외부 repo>` + `docker compose up -d` 정상 동작" stays unticked pending resolution above.
- M1-E1 cycle 1.7 (Sandbox Controller) and M1-E4 cycle 4.10 (dogfood) both depend on inner compose actually launching, so this *must* be unblocked before M1-E1 starts. The right time to open the upstream Sysbox issue is right after M0-P2 closes.

**Reproduction:**

```bash
cd poc/sysbox_isolation
sg docker -c "bash boot_integrated.sh"
sg docker -c "docker exec gapt-sysbox-poc docker run --rm hello-world"
# → unsafe procfs / cross-device link crash
```

---

## KI-2. `docker cp` into a SeaweedFS-FUSE-mounted directory is not reliable

**Surface:** `docker cp ./local.yml gapt-sysbox-poc:/workspace/sample-compose/docker-compose.yml` reports `Could not find the file /workspace/sample-compose` even after `docker exec ... mkdir -p` has created the directory and FUSE has acknowledged it.

**Workaround used in `check_inner_compose.sh`:** `docker exec -i ... tee` instead of `docker cp`. Same effect, completely deterministic over FUSE.

**Why:** `docker cp` uses an internal API path that resolves the destination on the daemon side before the kernel sees it; FUSE's stat returns successfully but the daemon-side resolution races against the FUSE cache. Not worth digging into — the workaround is a one-liner and the path doesn't go through `docker cp` in production anyway (sandbox content gets there via git clone, not host-side copies).
