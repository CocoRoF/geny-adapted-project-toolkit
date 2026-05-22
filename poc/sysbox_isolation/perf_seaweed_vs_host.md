# SeaweedFS Mount vs host bind — git operation performance

> Plan: [`../../docs/plan/m0/p2_isolation_seaweedfs.md`](../../docs/plan/m0/p2_isolation_seaweedfs.md) §7
> Progress: [`../../docs/progress/m0/p2_isolation_seaweedfs.md`](../../docs/progress/m0/p2_isolation_seaweedfs.md)

This file is the destination for the M0-P2 PR6 measurements. **Not yet populated.**

PR6 will:

1. Clone the same repository (size + history depth held constant) into three locations from inside the Sysbox sandbox:
   - (a) SeaweedFS-mounted `/workspace`
   - (b) inner-container host-side named volume (the inner docker overlay path)
   - (c) host bind mount for the control group
2. Run a fixed set of git operations 5 times each, record wall-clock:
   - `git status` (no diff)
   - `git log --oneline -100`
   - `git diff HEAD~5`
   - `git checkout <branch>`
   - `git add -A && git commit -m test && git reset --soft HEAD~1`
3. Populate the table below.
4. State a hybrid layout (e.g., `.git` on (b), worktree on (a)) if the numbers demand one, and revise [`decision_volume_driver.md`](decision_volume_driver.md) §"What this does not settle" accordingly.

## Results placeholder

| Operation | (a) SeaweedFS FUSE | (b) host named volume | (c) host bind | Ratio (a/c) |
|---|---|---|---|---|
| `git status` | *t.b.d.* | *t.b.d.* | *t.b.d.* | — |
| `git log --oneline -100` | *t.b.d.* | *t.b.d.* | *t.b.d.* | — |
| `git diff HEAD~5` | *t.b.d.* | *t.b.d.* | *t.b.d.* | — |
| `git checkout <branch>` | *t.b.d.* | *t.b.d.* | *t.b.d.* | — |
| `git add+commit+reset` | *t.b.d.* | *t.b.d.* | *t.b.d.* | — |

## Hybrid layout proposal (filled in by PR6)

*To be written once we have numbers.*
