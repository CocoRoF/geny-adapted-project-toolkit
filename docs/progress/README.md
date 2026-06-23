# progress/

The progress record for each cycle lives here.

## Usage rules

Per the cycle flow in [`../plan/00_master_plan.md`](../plan/00_master_plan.md) §0.5:

1. When a cycle starts → create a new `m{n}/{cycle_id}.md` file (Status: in_progress)
2. On every PR merge → append at least one line to the same file (date + PR link + one-line summary)
3. When a cycle ends → add a *drift section* at the bottom of the same file (how it differed from the plan) + Status: done

## Current cycle in progress

**M2 Phase A complete** (assistant-side). Awaiting user dogfood.
Once the 7-step scenario in [`m2_serve_capability.md`](m2_serve_capability.md) §6
passes → re-detail the original M2 outline (E1–E6).

## Cycle status index

| ID | Status | Progress file |
|---|---|---|
| M0-P1 | done | [`m0/p1_monorepo_ci.md`](m0/p1_monorepo_ci.md) |
| M0-P2 | done | [`m0/p2_isolation.md`](m0/p2_isolation.md) |
| M0-P3 | done | [`m0/p3_executor_agent.md`](m0/p3_executor_agent.md) |
| M1-E1 | done | [`m1/e1_backend_foundation.md`](m1/e1_backend_foundation.md) |
| M1-E2 | done | [`m1/e2_agent_and_git.md`](m1/e2_agent_and_git.md) |
| M1-E3 | done | [`m1/e3_web_ide_shell.md`](m1/e3_web_ide_shell.md) |
| M1-E4 | done | [`m1/e4_integration_dogfood_geny.md`](m1/e4_integration_dogfood_geny.md) |
| M1 post-close hardening | done | [`m1/_post_close_hardening.md`](m1/_post_close_hardening.md) |
| **M1.5** | done (assistant-side); user dogfood pending | [`m1_5_dogfood_readiness.md`](m1_5_dogfood_readiness.md) |
| **M2 Phase A** (serve capability) | done (assistant-side); user dogfood pending | [`m2_serve_capability.md`](m2_serve_capability.md) |

Entry conditions are in [`../plan/dependencies.md`](../plan/dependencies.md).
