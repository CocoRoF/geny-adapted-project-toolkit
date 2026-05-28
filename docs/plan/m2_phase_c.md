# M2 Phase C — Multi-project Operations

> Parent: [`m2_m5_outline.md`](m2_m5_outline.md) · [`00_master_plan.md`](00_master_plan.md)
>
> Scope reaffirmed: **single-admin self-hosted**. Phase C adds
> *concurrent project / branch operation* on top of that single admin.
> No multi-user, no RBAC, no permission boundaries.

---

## Entry

- ✅ Phase B-Hardening closed (B.H.1-4)
- ✅ User has chosen `C.1 → C.2` track (2026-05-28)
- C.3 (build cache) + C.4 (mobile PWA) deferred until C.1/C.2 lands

## Theme

"오늘 하루 안에서 N개 프로젝트 + 여러 브랜치를 동시에 굴린다."

---

## C.1 — Worktree-1st workspace model

Reframes `Workspace` as a real `git worktree` rather than an isolated
`git clone`. Result: cheap branch-switching (worktrees share objects),
strict `(project, branch)` uniqueness, and one-click "open this branch
in its own IDE".

### Design

- **Bare repo per project** at `${WORKSPACE_ROOT}/<slug>/.bare`
  (lazy — created on the first workspace for that project).
- **Workspace path** stays `${WORKSPACE_ROOT}/<slug>/<wid>` (no UX
  change). It's now a `git worktree` of the bare instead of a clone.
- **Create**: `git -C .bare fetch origin --prune` → `git -C .bare
  worktree add <wid> <branch>` (with `-b` when branch is new).
- **Delete (archive)**: `git -C .bare worktree remove --force <wid>` +
  best-effort `prune`. Worktree dir disappears; bare keeps objects.
- **Legacy rows** (workspaces created before C.1): stay as direct
  clones. Detected by absence of `.git/worktrees/<wid>` link inside
  the bare. Delete-path tolerates either layout.

### Uniqueness

Partial unique index — only enforced while a row is *not* archived:

```sql
CREATE UNIQUE INDEX ix_workspaces_project_branch_active
  ON workspaces (project_id, branch)
  WHERE status != 'archived';
```

Archived rows pile up freely (audit value).

### Reuse-on-create (idempotent POST)

`POST /workspaces` with `(project, branch)` that already has a
non-archived row returns the **existing** workspace (200 OK), not 409.
Lets the UI's "open branch X" action work whether or not the
workspace already exists, without a pre-check round-trip.

### Migration safety

Single-admin host, low row count, but still:
1. The data migration **archives** older duplicate rows for any
   existing `(project_id, branch)` collisions (keeps the newest).
2. **Then** creates the partial unique index.

### DoD (C.1)
- [ ] Workspace create on a never-seen branch → bare lazily created,
      `worktree add` succeeds, IDE works.
- [ ] Repeat-create with same `(project, branch)` returns same id.
- [ ] Delete cleans up the worktree dir + metadata entry.
- [ ] Legacy clone-based rows still delete cleanly.
- [ ] Tests: unique-constraint, idempotent-create, worktree
      add/remove, legacy-path delete.

---

## C.2 — Multi-project simultaneous UX

Builds on C.1's branch-as-workspace. The user is one admin operating
N projects at once; no permission story needed.

### Items

1. **C.2.a — Multi-project left tree**
   - `ProjectsIndex` route gains a sticky tree sidebar of all
     non-archived projects with their active workspaces nested under
     each.
   - Click a workspace → open IDE for that workspace.
   - Click a project → its detail.
   - Survives navigation (the tree itself isn't unmounted on route
     change inside `/projects/...`).

2. **C.2.b — Cmd/Ctrl+P palette**
   - Global keyboard palette (mac: ⌘P, others: Ctrl+P).
   - Mixes projects + workspaces + recent files into a single fuzzy
     list. Enter opens the most-specific match.
   - Fallback (no command): just opens the IDE quickfile for the
     current workspace.

3. **C.2.c — Per-project cost rollup**
   - Cost dashboard groups `agent_sessions.cost_usd` by `project_id`
     in addition to its existing single-project view.
   - "All projects" header with each project's contribution + totals.

4. **C.2.d — Active sandbox cap**
   - `GAPT_MAX_ACTIVE_SANDBOXES` setting (default 6).
   - Workspace create rejects with `workspace.cap_reached` when the
     count of `WorkspaceStatus IN (CREATING, RUNNING)` exceeds the
     cap.
   - UI: banner on `ProjectsIndex` when over 80% of cap.

### DoD (C.2)
- [ ] Two projects open in two tabs, two workspaces each, all four
      independent (chat / edit / deploy unaffected by sibling state).
- [ ] Cmd+P navigates between any project/workspace within 2 keypresses.
- [ ] Cost dashboard shows correct per-project totals.
- [ ] Spawning the (cap+1)-th workspace yields a clear UI error.

---

## Out-of-scope (Phase C punted)

- **C.3 — build cache** (BuildKit local cache): wait until a real
  deploy frequency is measured.
- **C.4 — mobile PWA**: wait until C.1/C.2 land.
- Per-project RBAC, sharing, audit isolation: covered by the
  permanent v1 single-admin decision.
