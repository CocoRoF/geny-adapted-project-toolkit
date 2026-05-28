# M2 Phase D — Agent UX polish

> Parent: [`m2_m5_outline.md`](m2_m5_outline.md) · [`00_master_plan.md`](00_master_plan.md)
>
> Phase D is the v1 endpoint. After D closes, the next planning pass
> is driven by real usage data, not a-priori design.

---

## Entry

- ✅ Phase B-Hardening closed (B.H.1-4) — 2026-05-28
- ✅ Phase C.1 → C.2 closed — 2026-05-28
- C.3 (build cache) + C.4 (mobile PWA) intentionally deferred until
  real usage confirms they're load-bearing

---

## D.1 — Plan / Act mode separation

The chat already has a local Plan/Act toggle that prepends a
text prefix in Plan mode, but the backend still grants every tool
either way. D.1 makes the mode **load-bearing**: in Plan mode the
agent literally cannot mutate.

### Design

- New `ChatModeRef` (mutable, per-session) attached to `SessionRuntime`.
- `POST /sessions/{sid}/invoke` accepts `mode: "plan" | "act"`; the
  runtime updates `mode_ref.mode` before kicking off the invoke task.
- The existing `policy_hook` (PRE_TOOL_USE) reads `mode_ref` and
  short-circuits to `block(...)` when `mode == "plan"` and the tool
  is in the mutation set (`gapt_edit`, `gapt_git`, `gapt_pr`).
- Read-only tools (`gapt_read`, `gapt_glob`, `gapt_grep`) fall
  through to the regular PolicyEngine evaluation in either mode.
- ChatPanel sends `mode` in the payload; visual lock badge appears
  in the header so the user can see the gate is real.

### Out-of-scope

- Mode persistence across browser reloads (frontend localStorage
  already does this via `useState` defaulting — explicit DB persistence
  is future work).
- Per-tool fine-grain ACLs (PolicyEngine already supports this; we
  layer the plan gate on top, not replace it).

### DoD
- [ ] `POST /sessions/X/invoke` with `mode=plan` followed by an agent
      turn that would call `gapt_edit` results in a `policy.deny`
      block + an SSE error frame quoting the mode.
- [ ] Same flow with `mode=act` succeeds.
- [ ] ChatPanel lock badge reflects the current mode.

---

## D.2 — Diff grouping + partial apply

Current state: each `gapt_edit` call emits one `DiffCard`. The user
either approves the whole turn or rejects it — there's no "apply this
file but not that one".

### Design

- Group consecutive `gapt_edit` cards within a single turn by file.
- Add a per-group checkbox; "Apply selected" calls a new endpoint
  (`POST /sessions/X/apply-diff-subset`) that takes the subset of
  edit IDs and stages only those.
- LLM-generated one-line summary per group is best-effort: omit
  when the agent didn't supply one (don't fall back to a generic
  string; the file path + edit count is good enough).

### Out-of-scope (deferred to v1.5)

- Cross-turn diff stitching (each turn is independent).
- "Reject + ask again" loop (out — the user can re-prompt manually).

### DoD
- [ ] A 3-file turn renders 3 grouped diff cards.
- [ ] Selecting 1 of 3 + Apply stages exactly that file.
- [ ] Existing single-file flow still works (back-compat).

---

## D.3 — Conversation persistence + resume

The SSE stream is durable across reconnects today (`since=` replay),
but **only within the same server process**. A backend restart wipes
the runtime cache and the chat re-mounts blank.

### Design

- New `session_events` table — append-only log of every SSE frame.
- Stream endpoint reads from DB when in-memory cache is empty.
- ChatPanel survives backend restarts.

### DoD
- [ ] Restart the server mid-conversation — the chat panel re-loads
      every message and tool call.
- [ ] Stream from a fresh tab catches up via replay before live-tailing.

---

## D.5 — Layout presets

Dockview already supports user-saved layouts. D.5 adds 4 named
presets (default / chat-focused / debug / minimal) selectable from
the IDE chrome.

### DoD
- [ ] Switching between presets is instant and stateless (presets
      stored client-side, no server round-trip).
- [ ] User-saved layouts survive a preset switch and back.

---

## D.4 — keyboard / palette

**Already shipped** by C.2.b (Cmd/Ctrl+K palette extended with
project + workspace navigation). The remaining items from D.4 in
the outline (file-open palette entry, "search chat" palette entry)
are out of scope for v1.

---

## v1 close criteria

After D.1, D.2, D.3, D.5: v1 spec complete. Sign off needs:
- A week of self-dogfooding with no critical bug
- One external user installs from scratch — note where they get stuck

Anything beyond v1 is driven by usage data.
