<!--
  GAPT PRs MUST reference the originating plan + progress cards.
  See CONTRIBUTING.md §3.
-->

## Plan / Progress (required)

- Plan: `docs/plan/...`
- Progress: `docs/progress/...`

## What changed

<!-- Short summary. The body of each commit already has the long form. -->

## Checklist

- [ ] CI green (server / runtime / web — and compose-smoke when relevant)
- [ ] Plan card linked above; progress card updated in this PR
- [ ] Docs updated if the change affects a contract or principle in `docs/00`–`docs/12`
- [ ] No secrets, `.env*`, `*.key`, `id_rsa*`, or other credentials added (see `.gitignore`)
- [ ] If the change touches isolation (`runtime/`, `compose/`, sandbox boot), I-1~I-9 scenarios from `docs/06 §6.10` were re-checked
- [ ] If the change touches PolicyEngine defaults or the 5 inviolable invariants (`docs/09 §9.2.4`), the rationale is explicit in the commit body

## Notes for the reviewer

<!-- Anything that helps the reviewer decide quickly: surprising
     trade-offs, deferred work, drift from the plan, etc. -->
