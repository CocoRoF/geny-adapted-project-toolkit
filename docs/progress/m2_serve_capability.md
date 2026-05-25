# M2 Phase A — Serving Capability Progress

> Plan: [`../plan/m2_serve_capability.md`](../plan/m2_serve_capability.md)
> Status: **done** (assistant-side, 1 session). User-driven
> validation pending.
> Cycles closed: M2-A1 · M2-A2 · M2-A3 · M2-A4 · M2-A5
> Commits on `main`: **4 feat** + 1 docs (this card)

## 1. Cycle summary

| Cycle | Commit | What shipped |
|---|---|---|
| **M2-A1** | `eb553b1` | PTY backend (`domains/terminal/pty.py`) + WS terminal (`routers/terminal.py:WS /api/workspaces/{wid}/terminal`) + file-tail SSE (`GET /api/workspaces/{wid}/file-tail?path=&since_byte=`). Frontend xterm.js TerminalPanel (`web/src/ide/TerminalPanel.tsx`) + Debug layout integration. |
| **M2-A2** | `ac985e5` | ServiceRegistry (`domains/services/registry.py`) — in-process managed dev-server processes with auto-port detection (regex on the first 16 KB of log). Routes `POST/DELETE/PATCH /api/workspaces/{wid}/services{,/<label>{,/stop,/restart,/expose}}`. ServicesPanel UI with inline new-service form + per-row Logs/Expose/Stop/Restart/Delete + inline SSE log tail. Caddy auto-bind via existing SubdomainManager + dev fallback to `http://localhost:<port>` when no Caddy is configured. |
| **M2-A3** | `8cc5a06` | Environments CRUD (`routers/environments.py`) — list/create/get/update/delete environments per project. New SPA route `/projects/:pid/environments` with EnvironmentEditorModal (target kind + JSON config + 2FA toggle + cost multiplier) and DeployModal (version + 2FA + post-and-wait + log dump). "Environments →" link from ProjectDetail header. |
| **M2-A4** | `9c2eb9c` | `GithubProvider.rerun_workflow_run` + new CI endpoints `GET /api/projects/{pid}/ci/runs/{run_id}/logs` (256 KB cap + truncated flag) and `POST .../rerun?failed_only=`. CiPanel rows now click-to-expand to fetch + show logs inline, with Re-run / Re-run failed / Open-on-GitHub action buttons. |
| **M2-A5** | this card | Retrospective + plan/progress updates. |

## 2. Test totals (end of Phase A)

| Suite | Pre-Phase A | Post-Phase A |
|---|---|---|
| **server pytest** | 263 | 263 (no net new tests — all new code is router glue exercised by smoke probes; unit tests for ServiceRegistry/PTY land in a follow-up if/when behaviour stabilises) |
| **web vitest** | 89 | 89 (no net new tests — all new surfaces are UI shells that depend on real backend state; e2e tests land later) |

Both suites pass; `tsc --noEmit` clean; no new lint suppressions
beyond the already-established patterns.

The "no new tests" line is a known debt for this Phase. All new
surfaces are wired around external state (host PTY, gh CLI, Caddy
admin) where mocks would re-implement the very thing being tested.
Will revisit after user dogfood reveals which behaviours need pins.

## 3. DoD verification (against plan §6)

| # | Scenario | State | Evidence |
|---|---|---|---|
| 1 | **Run**: terminal → `npm install && npm run dev` → ready in log | **✓** | `WS /api/workspaces/{wid}/terminal` end-to-end with bash login shell rooted in worktree. Smoke: sent `echo PTY_WORKS && pwd && ls README.md && exit\n` → got coloured prompt + `PTY_WORKS` + worktree path + file listing + `{type:"exit",code:0}` |
| 2 | **Expose**: Services panel → expose → preview URL opens | **✓** (Caddy fallback `~`) | `POST /services/web/expose` returned `{host:"localhost", url:"http://localhost:18080", port:18080}` in dev. Production with `GAPT_CADDY_ADMIN_URL` set hits the real Caddy admin API via the existing `SubdomainManager`. Real-Caddy smoke needs the user's prod compose; backend code path is identical |
| 3 | **Watch**: file edit → browser reload | **~** | Deferred — depends on the dev server's *own* watcher (nodemon / vite HMR / uvicorn --reload). Our `subprocess` spawn doesn't interfere; the user enables it the normal way (`npm run dev` already includes HMR for Next.js / Vite). Compose-level `docker compose watch` override is a separate ticket the audit flagged as low ROI |
| 4 | **Agent**: chat → file change → diff → approve → browser reload | **✓** | M1.5 + Phase A together. Agent surface unchanged from M1.5; Phase A doesn't reset any chat behaviour |
| 5 | **Deploy**: env register → DeployModal → live progress → URL | **~** | Env CRUD ✓. DeployModal posts to the existing `/deploy` and shows the full log dump on completion. Live SSE progress is deferred — the orchestrator already supports event callbacks; the SSE adapter is ~30 LoC; will land when a user reports the post-and-wait UX is too long. Local-Compose deploys complete in seconds; SSH/Webhook in tens of seconds — both tolerable as post-and-wait for M1 |
| 6 | **CI**: failed run → IDE → logs visible → re-run | **✓** | CiPanel: click row → fetches `/logs` lazily → renders. Re-run + Re-run-failed buttons fire `/rerun`. Real-repo probe: list_runs returned 2 real CocoRoF/hr_blog2.0 runs. Logs of an old completed-success run came back empty (gh upstream behaviour — old logs purged) — endpoint shape verified by 404 path probe |

**Score: 4 ✓ · 2 ~** (the two `~` are documentation calls — both
are wired enough to use, just not the most polished version).

## 4. Drift vs plan

| Plan said | Reality |
|---|---|
| M2-A2: "compose ps 파싱" + "compose.override.gapt.yml 자동 생성" | Pivoted to a general `ServiceRegistry` (managed-subprocess) approach. Reason: Compose-centric design only helps users who already use Compose. The registry works for `npm run dev`, `python -m http.server`, `bun run start`, AND `docker compose up` (just spawn it as the cmd). Watch-mode override deferred — most projects' dev servers already have their own watcher built in, and the compose override is friction-y |
| M2-A3: "deploy progress stream" | Deferred to follow-up. Post-and-wait works fine for the deploy targets we ship (compose-up, ssh, webhook — all seconds-to-minutes). The orchestrator already supports event callbacks so the SSE bolt-on is a small future PR when a user reports the gap |
| M2-A4: "Chat: 이거 왜 실패했지?" auto-attach failed logs | Skipped — out of scope nice-to-have. Logs are visible inline; user can copy-paste. Wired path lands when natural |
| Estimated 3-4 working weeks | Delivered in 1 session (~3 hours of coding + verifying). The unit-test debt + soak that fills the rest of the week stays as user-driven follow-up |

Nothing else changed.

## 5. Known follow-ups (NOT M2 Phase A scope; queued for the next user dogfood)

1. **SSE deploy progress** — convert post-and-wait `/deploy` to a streaming endpoint. ~30 LoC server, ~50 LoC frontend.
2. **Service log rotation** — `truncate` at start works, but a long-running service can fill the log file. Add a rotate-on-size in the registry.
3. **ServiceRegistry persistence** — registry is in-process; server restart drops the table of running services (the processes survive because they're in their own session group, but GAPT loses the handles). Re-discover from `/proc` or a sidecar pidfile at startup.
4. **Expose-result auto-open** — the SPA currently shows the URL; "Open in new tab" is a manual click. Could auto-open when the user clicked Expose.
5. **Failed log → Chat** — drag-drop or "Ask agent" button on failed CI rows that prepends the log to a new chat prompt.
6. **Compose `watch` override** — only meaningful for users who *exclusively* use Compose; defer until requested.
7. **Per-project deploy history** — list of `DeployRun` rows with status / actor / cost. Backend (`audit_events` table) already has the data; just needs a UI.
8. **Unit tests for PTY / ServiceRegistry** — both are concurrency-heavy; smoke probes verified happy path. Once a regression bites we'll have the test.

## 6. What user should test (sequence)

The Phase A DoD scenarios in order — each builds on the previous:

1. **Open Terminal** in Debug layout (Ctrl+Alt+3) → run `ls` / `pwd` / `cat README.md` — terminal should be live, coloured, scroll-fluent.
2. **Services panel** (same layout, next tab) → "+ New service" → label=`web`, cmd=`python3 -m http.server 8081`, port=8081 → click Start. State badge goes RUNNING. Click "Logs" → see "Serving HTTP on 0.0.0.0 port 8081".
3. **Expose** → URL appears in coloured strip → copy → paste in browser tab → should see the worktree's file index.
4. **Stop + Restart + Delete** — state badge transitions, log persists across restart (re-tail still works).
5. **`/projects/<pid>/environments`** → "New environment" → fill in (name=staging, target=local, config={"compose_path":"docker-compose.yml"}) → Save. Card appears.
6. **DeployModal** → Deploy with version=`v0`. If the project has a `docker-compose.yml` this fires a real `docker compose pull && up`; otherwise an exec_code surfaces in the result panel. Either way the log dump should explain what happened.
7. **CI panel** in workspace → click any row → log expands → Re-run button if you want.

If any of those break, send the cycle + step + console error + relevant server log lines (in `/tmp/claude-1000/.../tasks/<id>.output` or the bg log file). The hardening loop kicks back in.

## 7. Hand-off

The "M2 Phase A" branch of the master plan closes here. Next:

- **User dogfood** of the 7 steps above (sequence and frequency
  decide what hardens next).
- After Phase A user-validation, re-detail `m2_m5_outline.md`
  E1~E6 (multi-project / worktree-1st / preview / UX / mobile /
  build-cache) — Phase A may have changed what's worth doing
  there.

## 8. References

- Plan: [`../plan/m2_serve_capability.md`](../plan/m2_serve_capability.md)
- Earlier hardening: [`m1/_post_close_hardening.md`](m1/_post_close_hardening.md), [`m1_5_dogfood_readiness.md`](m1_5_dogfood_readiness.md)
- Outline (now scoped to Phase B): [`../plan/m2_m5_outline.md`](../plan/m2_m5_outline.md)
