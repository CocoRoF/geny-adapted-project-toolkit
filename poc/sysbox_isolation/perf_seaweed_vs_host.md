# SeaweedFS Mount vs host bind — git operation performance

> Plan: [`../../docs/plan/m0/p2_isolation_seaweedfs.md`](../../docs/plan/m0/p2_isolation_seaweedfs.md) §7
> Progress: [`../../docs/progress/m0/p2_isolation_seaweedfs.md`](../../docs/progress/m0/p2_isolation_seaweedfs.md)

Measured by `poc/sysbox_isolation/bench_git.sh`. Repo: `https://github.com/pre-commit/pre-commit.git`.
Each row is mean of 20 runs in milliseconds. `(a)` is option B from `decision_volume_driver.md`; `(b)` is the sandbox's own overlayfs root (transient); `(c)` is a host bind mount as a control.

## Numbers (2026-05-22T15:27Z, host: kernel 6.17, sysbox-ce 0.7.0)

| Operation | (a) SeaweedFS FUSE | (b) inner overlayfs | (c) host bind | Ratio (a/c) |
|---|---|---|---|---|
| status        |  16 ms |  6 ms |  3 ms |  **5.33x** |
| log           |   9 ms |  3 ms |  3 ms |  **3.00x** |
| diff          |  10 ms |  8 ms |  4 ms |  **2.50x** |
| switch_back   | 437 ms | 20 ms | 18 ms | **24.28x** |
| commit_reset  |  67 ms | 12 ms | 10 ms |  **6.70x** |

## Reading

- **Read-only ops (`status`/`log`/`diff`)**: 2.5~5.3x slower on FUSE. Single-digit milliseconds in absolute terms — *imperceptible* in interactive use. FUSE is fine here.
- **`commit_reset`**: 6.7x slower, but still tens of milliseconds. Acceptable for the "save a file then commit" loop that GAPT's chat-driven flow generates.
- **`switch_back` (branch checkout)**: **24x slower (437 ms vs 18 ms)**. This is the headline finding. `git checkout` writes hundreds of files into the working tree, each one a separate FUSE round-trip. The asymmetry isn't FUSE-the-protocol's fault — it's the *number of small writes* vs the *batched ext4 page cache* on the host bind.

## Decision

The numbers are *not bad enough* to require a hybrid layout for M0~M2:

- Option B (single FUSE mount on `/workspace`) **stays the default**. The cost is one ~400 ms blip per `git checkout`. Sub-second; a user notices but doesn't break flow.
- We do **not** split `.git/` onto a host volume yet. Splitting buys ~400 ms on checkout in exchange for substantial extra plumbing (two mounts to coordinate, persistence rules, backup paths). At M2 when the dogfood workflow has actual checkout cadence data we'll know whether the trade is worth it.
- M4 multi-node will revisit *both* dimensions (FUSE perf and the hybrid layout) when SeaweedFS goes from single-volume-on-disk to a real cluster. Those numbers will be different enough to repeat the benchmark before re-deciding.

`decision_volume_driver.md` is updated to reflect this — the "What this does not settle" section is replaced with a settled-by-numbers reference back here.

## Reproducing

```bash
cd poc/sysbox_isolation
sg docker -c "bash bench_git.sh"
# results overwrite this file
```

Knobs:

- `BENCH_REPO_URL` — switch to a different repo (e.g. larger one for harder numbers)
- `BENCH_REPO_REF_OLD` — what `switch_back` checks out
- `HOST_BENCH_DIR` — where the (c) bind mount points

## Caveats

- Single host, single run. Numbers are *indicative*, not statistically rigorous — for that we'd want N≥5 *fresh-cache* runs and explicit warm/cold separation. M2's perf cycle can do that properly.
- `pre-commit/pre-commit` is medium-small (~1200 files). A repo with 10× more files would push checkout to ~4 seconds on FUSE; that's the kind of size where a hybrid layout starts paying for itself.
- The (b) "inner overlayfs" column is the sandbox's *root filesystem*, not a real persistent location — included only to show the FUSE overhead vs the same kernel running outside FUSE; **do not** use that path for real data in production.
