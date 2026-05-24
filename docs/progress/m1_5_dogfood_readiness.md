# M1.5 — Dogfood Readiness Progress

> Plan: [`../plan/m1_5_dogfood_readiness.md`](../plan/m1_5_dogfood_readiness.md)
> Status: **done** (assistant-side). User-driven validation still
> pending — see §6.
> Cycles closed: 1.5-A · 1.5-B · 1.5-C · 1.5-D · 1.5-E · 1.5-F.
> Total commits on `main`: **5** (`dd7b1da` → `dffd18a`).

## 1. Cycle summary

| Cycle | Commit | What shipped | Tests added |
|---|---|---|---|
| **1.5-A** | `dd7b1da` | Files API host execution (MockSandboxBackend) · Settings UI for `github_token` / `openai_api_key` / `anthropic_api_key` · `credentials_resolver` propagating user-scoped secrets into sandbox env + host clone via `http.extraHeader=Authorization: Basic <b64>` | 0 net (mock backend change didn't break existing) |
| **1.5-B** | `92a3f7c` | FileEditor Tailwind-styled (header / status / empty / loading / binary states) · Monaco dark theme wired through `useTheme` | 0 net (existing 4 cases reframed) |
| **1.5-D** | `aaa298a` | `GET /api/workspaces/:wid/diff` (working-tree vs HEAD, +/-/U status, unified diff, 256 KB cap) · `DiffPanel.tsx` with per-file rail + +/- gutter colors · replaces the last `placeholder` panel | server +5 |
| **1.5-C** | `a5ba5ac` | `build_for_session` reads user-scoped vault keys (`anthropic_api_key`, `openai_api_key`, `google_api_key`) → claude_code_cli + SDK providers · `routers/sessions.create_session` now passes `vault=...` (was `None`) | server +2 |
| **1.5-E** | `dffd18a` | CI panel uses user's vault `github_token` first, server-wide env as fallback · `secrets_router.set_vault(None)` lets tests clear the singleton | server +1 |

## 2. Test totals (end of M1.5)

| Suite | Pre-M1.5 | Post-M1.5 | Delta |
|---|---|---|---|
| **server pytest** | 350 | **358** | **+8** |
| **web vitest** | 89 | **89** | 0 (Editor test reframed, not net new) |

All run green against the real test Postgres + the mock sandbox.
Typecheck (`tsc --noEmit`) clean. `ruff` + `mypy` not re-run in this
cycle — no new patterns introduced; existing CI gates them.

## 3. DoD verification

Re-checking the 9 DoD rows from
[`../plan/m1_5_dogfood_readiness.md`](../plan/m1_5_dogfood_readiness.md):

| # | Scenario | State | Evidence |
|---|---|---|---|
| 1 | Settings → `github_token` save → new workspace clones a private repo | **~** assistant-confirmed wired; user must test against a real private repo | `_default_clone_runner_with_creds` smoke-tested against public repo; private repo requires user-issued PAT |
| 2 | Tree click → Editor shows file | **✓** | Files API returned real entries; Editor renders Monaco; e2e probed `GET /api/workspaces/:wid/file?path=/README.md` |
| 3 | Edit + autosave | **✓** | `PUT /api/workspaces/:wid/file` end-to-end on the live dev instance — wrote and verified on disk; debounced autosave wires to `flushSave` |
| 4 | Chat session → first token + cost | **~** | Wiring fully verified; `secret.read purpose=agent_session.anthropic_api_key` + `agent.credentials.built providers=['anthropic', 'claude_code_cli']` fire on invoke. *But*: the executor stage-6 emits no text events on the dev box (see §4) — out-of-scope geny-executor issue |
| 5 | Tool call → ToolCallCard render | **~** | Card wiring exists from M1-E3 Cycle 3.9; depends on §4 |
| 6 | Diff visible | **✓** | DiffPanel + `/api/workspaces/:wid/diff` shipped (1.5-D); smoke-tested with a one-line README.md edit on the live worktree |
| 7 | Commit + PR via executor tool | **~** | `GITHUB_TOKEN` + `GH_TOKEN` now flow into the sandbox env (1.5-A); depends on §4 |
| 8 | CI panel shows the PR's run | **✓** wiring | 1.5-E hooked user-vault token into the router; reaches `gh run list` once §4 is unblocked and a real PR exists |
| 9 | $1 cycle budget + clean audit | **~** | Budget guard is wired (`max_budget_usd=1.0` in `build_claude_code_cli_creds`); audit is producing events for every secret read + session create. Final tally awaits §4 |

**Score: 3 ✓ · 6 ~** (was projected as "1 ✓ before M1.5 starts").

## 4. Known blocker — geny-executor stage-6 silent completion

When a real session is invoked against `gapt_default` manifest with a
real `anthropic_api_key` available:

- `Pipeline.run_stream(message)` completes silently — `_run_with_lifecycle`
  reaches the `else` branch and publishes a `done` event with
  `cost_usd=0, input_tokens=0, output_tokens=0`.
- No `text` / `tool_call` / `tool_result` events are mapped by
  `_map_pipeline_event` — meaning either none were emitted by the
  pipeline, or their event-type strings don't match the
  `text` / `*.chunk` / `tool*` patterns.

Per [[feedback_extend_executor_not_adapter_layer]] the fix belongs
**upstream in geny-executor**, not in a GAPT-side adapter. Candidate
root causes (to be triaged by reproducing against
`poc/executor_agent/run.py` from M0-P3):

1. `claude_code_cli` provider's stream path silently fails when run
   without an active OAuth subscription, even with an explicit
   `api_key`.
2. The CLI subprocess starts but its stdout is consumed by a stage
   that doesn't re-emit it as a `pipeline` event.
3. `_classify_cli_result` patch already flagged in M0-P3 (the
   `exec.cli.permission_denied` stream-path patch).

Action: open a separate cycle (likely against geny-executor) — not
counted in M1.5 since it falls outside the GAPT codebase.

## 5. Drift vs plan

| Plan said | Reality | Why |
|---|---|---|
| 1.5-A is just "commit + smoke test" | Was already done in the prior session; this cycle's commit + push was a 5-min step | Plan was written *after* the work, not before — natural drift |
| 1.5-B "depending on what breaks" | Editor was using legacy `ide-editor-*` class names from before the Tailwind v4 rewrite. Zero CSS attached. Patched + wired Monaco dark theme that was a TODO from M1-E3 Cycle 3.14 | Plan correctly bet there'd be small breakage; size matched |
| 1.5-C "verify wiring, fix what breaks" | Found `build_for_session` reading `os.environ["ANTHROPIC_API_KEY"]` instead of vault, and the sessions router passing `vault=None`. Both real fixes. Did not fix the stage-6 silent-completion issue (out of scope) | Plan accurately predicted "this is the riskiest cycle"; under-budgeted at 1–2 PRs (delivered as 1, but the upstream blocker means a follow-up cycle is needed) |
| 1.5-D "minimal Diff panel" | Shipped close to the planned shape — single endpoint, single panel, no `<DiffEditor>` (Monaco's heavy diff component), just unified-text + per-file rail. ~300 LoC server + 200 LoC web. | Plan called this out as the only cycle with *new code*; size matched |
| 1.5-E "verify push/PR path" | The pre-1.5 CI router exclusively used `GAPT_CI_GITHUB_TOKEN`. Patched to prefer user-vault. Workspace clone token wiring was already done in 1.5-A | Plan said "0~1 PR"; delivered 1. Found one real bug (no per-user CI token), fixed it. |

No cycles were skipped or merged. No new analyses created.

## 6. What the *user* still needs to do

The DoD `~` rows above translate to user-driven steps:

1. **Save a real `github_token` in Settings**, then create a workspace
   from a private repo to confirm DoD #1.
2. **Save a real `anthropic_api_key` in Settings** (the user already
   did this — secret id `01KSDCE8TXE1TMPM3DBKSS1H1N`).
3. **Wait on the geny-executor fix in §4**. Once that lands and the
   GAPT side pulls the new version, DoD #4/#5/#7/#9 become testable.
4. **Open one PR end-to-end from the IDE** to close DoD #7+#8 +
   M1 DoD #1 (dogfood). Then capture an after-action review in
   `docs/analysis/<date>_first_dogfood_pr.md`.

## 7. Hand-off to M2

M2 ([`../plan/m2_m5_outline.md`](../plan/m2_m5_outline.md)) blocks
on:

- The user's PR-from-IDE pass (above).
- A 1-week soak of the dev compose to surface leaks.
- The upstream geny-executor stage-6 fix.

M2-E1 (multi-project), M2-E2 (worktree-1급), M2-E4 (UX 다듬기: Plan/Act,
diff group-Approve) all get cheaper once §4 is unblocked because the
inner-loop e2e becomes assistant-testable, not just assistant-verifiable.

## 8. References

- Plan: [`../plan/m1_5_dogfood_readiness.md`](../plan/m1_5_dogfood_readiness.md)
- Earlier hardening: [`m1/_post_close_hardening.md`](m1/_post_close_hardening.md)
- M1 close: [`m1/_summary.md`](m1/_summary.md)
- M0-P3 finding the 1.5-C blocker traces to: `analysis/` (PoC era)
- [[feedback_extend_executor_not_adapter_layer]] — why §4 fix belongs
  upstream
- [[feedback_durable_instructions]] — PR cadence + plan/progress
  pairing
