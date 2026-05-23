# M1 Summary — first integrated workflow

> Closes the M1 milestone (M1-E1 backend → M1-E2 agent+git → M1-E3 web
> IDE shell → M1-E4 integration/dogfood/Geny). Compiled at the end of
> Cycle 4.12. Carries the DoD checklist + a hand-off note for the user
> who will execute the actual dogfood + Geny first-adapt cycles.

## DoD against `docs/11_roadmap.md` §11.3

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | User can maintain GAPT *via* GAPT (dogfood) | **~** assistant-side ready; user execution pending | [`docs/operations/install.md`](../../operations/install.md) §6 procedure |
| 2 | User completes 1 Geny cycle without an external IDE | **~** infra + runbook ready; user execution pending | [`docs/operations/geny-adapt.md`](../../operations/geny-adapt.md) |
| 3 | Golden paths G1–G4 work | **✓** | See per-cycle progress cards (E1–E4) |
| 4 | Isolation scenarios I1–I9 covered by CI | **✓** for the 6 scenarios that don't need Sysbox real-runtime; **~** for the 3 that do (sandbox/test_*) | [`server/tests/sandbox/`](../../../server/tests/sandbox/) + [`server/tests/e2e/`](../../../server/tests/e2e/) |
| 5 | LLM cost live in session header | **✓** | `web/src/chat/` cost snapshot + `/api/sessions/:sid/stream` cost events |
| 6 | First-token latency ≤ (API + 100 ms) | **~** depends on Anthropic API + network; instrumentation in place to measure | Cycle 4.7 `/metrics` exposes counters; full SLA test belongs in user-driven soak |

Notation: **✓** done · **~** partial · **✗** missing.

## What shipped in M1-E4 (Cycles 4.1 – 4.11)

| Cycle | Surface | Key deliverable |
|---|---|---|
| 4.1 | server | `DeployTarget` Protocol + `LocalCompose / RemoteSsh / Webhook` adapters |
| 4.2 | server + API | `DeployOrchestrator` (policy → 2FA → secrets → target → audit), `POST /api/environments/:eid/deploy` + `/rollback` |
| 4.3 | server + web | `GET /api/projects/:pid/ci/runs` + CI panel (scope-reduced: manual refresh, no SSE) |
| 4.4 | server | Caddy admin-API client + dynamic subdomain registration + HMAC share links |
| 4.5 | server | PolicyEngine L2 server-YAML overrides with invariant floors |
| 4.6 | server + web | Audit dashboard — CSV/JSONL export + date range + pagination |
| 4.7 | server + web | Cost dashboard endpoints + `/metrics` Prometheus exporter + CostPanel |
| 4.8 | server + web | Notification service + Slack/Discord channels + header bell |
| 4.9 | server | `POST /api/sessions/oneshot` headless API for M5 cron/webhook |
| 4.10 | ops + server | `compose/docker-compose.prod.yml` + Caddy on-demand TLS `ask` gate + `docs/operations/install.md` |
| 4.11 | server + ops | Multi-file `compose_paths` chain + `docs/operations/geny-adapt.md` runbook |

## Tests + LoC (end of 4.11)

- **Server**: 350 pytest cases, 91 source modules, ruff + mypy clean.
- **Web**: 89 vitest cases, eslint/prettier/typecheck/build all clean.
- **Total commits in M1-E4**: 11 (one per cycle).

## What the user still needs to do (4.12+)

These are user-driven exercises the assistant cannot perform:

1. **Dogfood execution** — run [`install.md`](../../operations/install.md)
   §6 on the user's own VPS. Open a PR from inside the GAPT
   workspace, merge it, watch the prod deploy. Record the run in a
   fresh `progress/m1/dogfood_first_cycle.md`.
2. **Geny first adapt** — follow
   [`geny-adapt.md`](../../operations/geny-adapt.md) end-to-end.
   Goal: one Geny PR merged without opening Cursor / VS Code.
   Capture the after-action review at
   `analysis/{date}_geny_first_adapt_lessons.md`.
3. **Soak test** — leave the dev compose running for a week and
   record any memory / file-descriptor leaks. Cycle 4.7's `/metrics`
   has `gapt_sessions_active` + `gapt_sandbox_count{state}` for the
   leak hunt; Prometheus + Grafana ship in the same compose under
   `--profile metrics`.
4. **Demo video** — 3-minute golden-path screencast for the README
   landing.
5. **Anything deferred** in any cycle's "Plan 카드 대비 변경" section
   (e.g. ARQ-based CI poller in 4.3, per-user notification settings
   in 4.8, project-scoped API tokens in 4.9) — folded into M2.

## Hand-off to M2

The M1 base gives M2 the following building blocks to extend:

- **PolicyEngine** is layered (L1 builtin + L2 server YAML), so L3
  (org DB) and L4 (project DB + `.gapt/policy.yaml`) plug in without
  reshaping the engine.
- **DeployOrchestrator** is target-agnostic; K8sTarget (M4) lands as a
  new `DeployTarget` impl.
- **NotificationService** has a `Channel` protocol — Email / SMS
  channels add without touching the dispatcher.
- **`/api/sessions/oneshot`** is the M5 cron-scheduler trigger
  interface; M5 only needs to add the scheduler UI + project API
  token auth.
- **Prometheus `/metrics`** + the live `gapt_*` counters are the
  observability seam M3 onwards relies on.

Nothing in M1 was built behind a flag that needs flipping in M2 —
M2 is purely additive.
