# M1 Post-Close Hardening — 2026-05-24 → 2026-05-25

> M1 was declared "assistant-side complete" in [`_summary.md`](_summary.md)
> on 2026-05-24. The user immediately tried to dogfood by cloning
> `https://github.com/CocoRoF/hr_blog2.0` through the live deployment at
> `https://gapt.hrletsgo.me/`. That first contact revealed a series of
> bugs the assistant test suite (mocked sandbox + canned responses)
> could not surface. This card captures every fix shipped after the
> M1 close commit (`cdf12b2`).

## 0. Why this card exists

The M1 DoD chart said "3 ✓ · 3 ~". The three `~` items were *user-
execution* tasks (dogfood / Geny adapt / soak). What the chart did **not**
say: a serious chunk of the surface area was unreached by automated
tests, because the tests mock `SandboxBackend.exec_in` with canned
responses. That mock returned empty stdout for *every* file-system call
— so the Files panel, file open, and write paths silently no-op'd in
the real backend. Below is the drift.

## 1. Commits shipped after M1 close

Branch is still `main` — fixes were urgent enough to ship straight, not
behind a "M1.5" or feature flag.

| Commit | Surface | What it changed | User-visible symptom it cured |
|---|---|---|---|
| `c0d565a` | web | Full Tailwind v4 design system + shadcn-style primitives (Button, Input, Card, Badge, Modal, ConfirmDialog) | UI was raw HTML — "이 개 병신같은 디자인은 도대체 뭐야" |
| `a539bbe` | infra | Vite proxy + Cloudflare tunnel `gapt.hrletsgo.me` → `:5173` | No public URL to reach the dev SPA from a phone / outside the host |
| `375e87f` | server | Host-side `git clone` on workspace.create | Worktree existed but was empty — no real files |
| `46879ce` | server | 3× retries on transient git errors | First-attempt failures on Korean ISP → GitHub link |
| `f2befc3` | server | Clone timeout 600 s/attempt (was 180 s) | Large-asset repos clipped mid-receive |
| `44ab08c` | server,web | Non-blocking clone via `asyncio.create_task` + status=CREATING polling | API hung 30 s+; 524 from Cloudflare; "지금 모달에서 대충 보여주는건 뭐야" |
| `25d532d` | auth | `dev_token` returned in magic-link response → auto callback | User had to fish the token out of server logs |
| `060edda` | server,web | Workspace + project lifecycle actions (delete/stop/start) idempotent across mock-backend restarts (`swallow_missing=True`) | 409 "unknown sandbox" after every server restart |
| `b00fe23` | server,web | Live clone log (`git clone --progress` byte-streamed) + orphan `creating` cleanup on app startup | Stuck "creating" for 10+ min with no signal |
| `a8b12ea` | server | Clone log lives *sibling* of worktree (`{parent}/.{basename}.clone.log`) | `fatal: destination path already exists` because we wrote the log inside the empty-required dir |
| `56e5beb` | server,web | `list_for_project(include_archived=False)` default + `?include_archived=true` opt-in | Archive button just hid the row from no list — clutter |
| `dd77783` | web | Inline `InlineCloneLog` collapsible under each creating/failed workspace row in `ProjectDetail` | Log only visible after navigating into the IDE — invisible during creation |

**Plus uncommitted (this session, 2026-05-25)** — to be folded into one
commit:

| Surface | What changed |
|---|---|
| `server/src/gapt_server/domains/sandbox/mock_backend.py` | `exec_in` now runs argv on the host via `asyncio.create_subprocess_exec` and tolerates a missing sandbox state (DB row outlives the in-memory mock across restarts). Was returning empty stdout → Files panel empty. |
| `server/src/gapt_server/domains/workspaces/service.py` | New `_default_clone_runner_with_creds` injects `http.extraHeader=Authorization: Basic <b64(x-access-token:<PAT>)>` so the token never lands in argv. New `credentials_resolver` callable wired in via `__init__`; `_resolve_credentials` runs before `_boot_sandbox` so the credentials also flow into `SandboxCreateSpec.env` (`GITHUB_TOKEN`, `GH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` + `KEY_NAME.upper()` fallback). |
| `server/src/gapt_server/routers/workspaces.py` | `get_workspace_service` now also takes `SecretVault`. Builds a resolver that opens a fresh background session, lists user-scoped secrets, audits each read via `vault.read(..., purpose="workspace.boot", actor_id=...)`. |
| `web/src/api/secrets.ts` (new) | `listSecrets / storeSecret / rotateSecret / deleteSecret`. |
| `web/src/routes/Settings.tsx` | Real Settings UI: profile card + Credentials section with `github_token`, `openai_api_key`, `anthropic_api_key` rows. Save / Rotate / Delete + Eye toggle + ConfirmDialog. |

## 2. Why these gaps weren't surfaced earlier

| Gap | Why automated tests missed it |
|---|---|
| MockSandboxBackend `exec_in` stub returns empty stdout | Every file-system test uses `set_exec_response(...)` to inject canned stdout. Without that injection, the default empty return *looks fine* — no assertion failed because no code-path *exercised* the default branch with real data. |
| Workspace create hung the API | Tests use the synchronous in-request clone path with a `_noop_clone` stub. The real default runner had no timeout/retries; nobody timed the e2e clone. |
| Workspace delete/stop 409 on second server boot | Mock backend in-memory; tests don't restart the process between create and stop. |
| Magic-link UX requires log-scraping | Dev test had `dev_callback_url` but the SPA didn't consume it. Manual UX test never happened. |
| Clone log inside worktree | `_check_worktree` saw the dir as "non-empty" because the log was already there. The unit test stubbed the runner so this path didn't fire. |
| Settings page was placeholder | Roadmap deferred to M2; nobody noticed it's the first thing a new user opens. |

## 3. Lessons

1. **Mock backends with no-op defaults hide bugs.** The mock should either crash on un-canned calls (so the test author *has* to think about it) **or** fall back to real host execution (the path we took today — dev-only, safe because the worktree path is already host-side). We took the latter.
2. **"Soak" + "dogfood" are not optional follow-ups.** They are the *first* test of the surface, not a victory lap.
3. **Status reports should call out which DoD items are unverified end-to-end**, not just "assistant-side ready." We marked M1 as "complete" when the first user action revealed 10+ shipping bugs.

## 4. State as of 2026-05-25 (end of session)

| Surface | State |
|---|---|
| `/projects` (index) | Works. Archive + delete confirm dialogs land cleanly. |
| `/projects/:pid` (detail) | Works. Live clone progress inline. Trash icon per row. |
| `/projects/:pid/workspaces/:wid` (IDE) | Works for Files + Editor (Monaco) + Chat shell + Audit + CI + Cost + Preview. Diff is still the placeholder. |
| `/settings` | Works. Profile + GitHub PAT + OpenAI/Anthropic API keys. Stored encrypted; propagated to sandbox env + host clone. |
| `/cost` | Works (M1-E4 4.7). |
| Auth / magic-link | One-click in dev/staging. |
| Sandbox lifecycle | Idempotent across restarts (`swallow_missing=True` + on-the-fly `exec_in` host fallback). |
| `Files` API | Returns real entries (host execution through the mock). |
| Clone | Authenticated when `github_token` is set; anonymous otherwise. Retries × 3, 600 s timeout each, live progress streamed to `{parent}/.{basename}.clone.log`. |

## 5. What's still unverified by *me* end-to-end (assistant)

- **Open a file → edit → save → diff display.** Editor renders, save endpoint exists; the apply/diff flow needs a real chat session to confirm.
- **Start a Chat session → first executor tool call → cost display.** The shell wires `/api/sessions/stream`; needs a manifest selection + real run.
- **Deploy modal end-to-end** (Cycle 4.2 surface from a real UI button, not just curl).
- **CI panel** — needs a project with real GitHub Actions runs.
- **Preview iframe** — needs Caddy admin API live.

These are the focus of the next plan card.

## 6. References

- Plan: [`../../plan/m1/e4_integration_dogfood_geny.md`](../../plan/m1/e4_integration_dogfood_geny.md)
- Earlier status: [`_status_report_2026-05-24.md`](_status_report_2026-05-24.md)
- M1 close summary: [`_summary.md`](_summary.md)
- Next plan: [`../../plan/m1_5_dogfood_readiness.md`](../../plan/m1_5_dogfood_readiness.md)
