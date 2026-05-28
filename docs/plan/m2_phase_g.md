# M2 Phase G — Chat fortification

> Parent: [`00_master_plan.md`](00_master_plan.md) · [`m2_m5_outline.md`](m2_m5_outline.md)
>
> User-requested chat capability upgrade. Five cycles, sequential.
> Source material: the Geny sibling repo's
> `frontend/src/components/tabs/ClaudeCodeAuthModal.tsx` (717 LOC)
> and `backend/controller/llm_backends_controller.py` (902 LOC)
> already implement what we need — Phase G is **port + adapt**, not
> greenfield design. Per [[feedback_extend_executor_not_adapter_layer]]
> we route through `geny_executor` rather than introducing a new
> LLM adapter layer in GAPT.

---

## Why now

Phase B/C/D/E built the *platform*. Phase G makes the *agent
runtime* actually usable end-to-end without dropping to a terminal:

1. Today the chat hard-codes `gapt_default` and there's no UI to
   pick / inspect manifests.
2. Today Claude CLI auth happens silently inside the host shell;
   the user has to know that `claude auth login` exists.
3. Today the four bundled provider integrations (anthropic /
   openai / google / vllm) are wired in `credentials.py` but no
   manifest references them and no UI shows their state.

After Phase G, the user can:
- See every backend's health from Settings.
- Authenticate Claude Code CLI in four supported ways from the
  modal.
- Pick a manifest (default / planning / review) per session.
- Override the model + key knobs without touching env vars.
- Swap providers (claude_code_cli ↔ anthropic_sdk ↔ openai) when
  the bundled manifest variants justify it.

---

## G.1 — Claude Code CLI auth modal

The largest cycle. Ports Geny's auth flow into GAPT's
single-admin auth + vault model.

### Backend (`server/src/gapt_server/routers/llm_backends.py`)

```
GET    /_gapt/api/llm-backends/health
       → grid of every provider's health: claude_code, anthropic,
         openai, google, vllm.

POST   /_gapt/api/llm-backends/cli/claude-code/recheck
       → forced re-probe (the user pressed "refresh").

GET    /_gapt/api/llm-backends/cli/claude-code/auth/status
       → wraps `claude auth status --json` so the modal knows
         whether the host already has a subscription / console-key /
         nothing.

POST   /_gapt/api/llm-backends/cli/claude-code/auth/login
       → spawns `claude auth login` (or with `--console` for the
         API-key path) as a background job, returns a `job_id`.

GET    /_gapt/api/llm-backends/auth/jobs/<id>/events  (SSE)
       → streams stdout/stderr from the auth job so the modal can
         show the device-code URL the user must visit AND the
         "Auth code:" prompt the CLI asks for.

POST   /_gapt/api/llm-backends/auth/jobs/<id>/input
       → forwards the user-typed auth code to the subprocess's
         stdin (the device-code flow's second leg).

POST   /_gapt/api/llm-backends/auth/jobs/<id>/cancel
       → SIGTERM the job, drop the registration.

POST   /_gapt/api/llm-backends/cli/claude-code/auth/logout
       → `claude auth logout`.

POST   /_gapt/api/llm-backends/cli/claude-code/setup-token
       → save a `claude setup-token` value into the SYSTEM-scoped
         vault under key `claude_setup_token`. The executor reads
         it via the existing `CredentialBundle` flow.

POST   /_gapt/api/llm-backends/cli/claude-code/test
       → fires a 1-shot `claude --print "ping"` and reports
         exit+stderr so the user can verify auth before chatting.
```

### Backend services

- `domains/llm_backends/auth_jobs.py` — registry of in-flight auth
  subprocesses (bounded, age-reaping). Mirrors Geny's `_AuthJob`.
- `domains/llm_backends/health.py` — per-provider `ProviderHealth`
  shape: `{provider, state: "ok"|"missing"|"expired"|"unknown",
  detail, last_checked_at}`.
- `domains/llm_backends/claude_oauth.py` — reads
  `~/.claude/.credentials.json` (or whatever the host claude
  stores) to extract `claudeAiOauth.expires_at_ms` so the modal
  shows "Pro plan, expires in 22d".

### Frontend (`web/src/settings/ClaudeCodeAuthModal.tsx`)

Modeled on Geny's modal but using GAPT's UI primitives (Button,
Card, Field, etc.) and i18n catalog. Four radio choices:

1. **호스트 마운트 (기본)** — informational: GAPT already mounts
   `~/.claude` into workspace containers (via
   `WorkspaceSandboxManager.host_claude_dir`, Phase B). Modal
   shows "currently mounted: yes/no" + status badge.
2. **모달에서 로그인** — starts the device-code job, displays the
   `https://...` URL the user must visit + an input for the
   pasted auth code.
3. **Setup 토큰 붙여넣기** — single text field, saves to vault.
4. **API 키 (Console)** — same shape, saves to vault as the
   `anthropic_api_key` secret (existing key).

### DoD (G.1)
- [ ] Modal opens from Settings → "Connect Claude Code CLI" button.
- [ ] Device-code path: URL surfaced, auth-code input submits, on
      success the status pill flips to "logged in".
- [ ] Setup-token + API-key paths persist to vault, "test" runs
      cleanly.
- [ ] Logout works.
- [ ] Auth status SSE stream cleans up on modal close.

---

## G.2 — LLM backends overview page

A new section in Settings: 5-card grid of provider health (claude_code,
anthropic, openai, google, vllm). Each card:

- State badge (ok / missing key / expired / unreachable).
- "Set credentials" button → opens the relevant modal/field.
- "Recheck" button.

No multi-provider switching yet — that's G.5. This is **visibility**:
the user can see whether a given backend is ready before they pick
the manifest that uses it.

### DoD
- [ ] Cards render correctly when ALL providers are unconfigured
      (no toasts of `500`, just neutral cards).
- [ ] Setting an `OPENAI_API_KEY` env var → card flips to ok on
      next refresh.

---

## G.3 — ChatPanel manifest picker

`env_id` is already plumbed through `createSession`. Add a dropdown
to ChatPanel's header (next to the `gapt_default` chip) that lists
the bundled manifests (`gapt_default`, `gapt_planning`, `gapt_review`)
+ optionally workspace-local `.gapt/manifests/*.json` discovery.

### DoD
- [ ] Picking a different manifest before starting the session
      starts that pipeline (visible in event stream).
- [ ] Default selection is `gapt_default`.
- [ ] Selection is sticky per project (localStorage).

---

## G.4 — Per-session model override pill

The Settings page's Pipeline overrides are *global*. Add a quick
per-session override in ChatPanel: click the manifest pill → small
popup with model / max-tokens / permission_mode fields. Applied
only to that session via the existing `InvokeRequest.mode` style
plumbing (extend it).

### DoD
- [ ] Override applies to next `invoke()` call without changing
      the global prefs.
- [ ] Clearing the popup reverts to global prefs.

---

## G.5 — Multi-provider manifest variants

Ship three more bundled manifests:

- `gapt_anthropic_sdk.json` — same stages as `gapt_default` but
  `stages[6].config.provider = "anthropic"` (uses the
  `ANTHROPIC_API_KEY` from vault instead of the CLI).
- `gapt_openai.json` — `provider = "openai"`, `model = "gpt-4o"`
  default.
- `gapt_google.json` — `provider = "google"`, gemini-pro default.

Settings: a single "default manifest" selector so the operator can
switch the workspace-wide baseline.

### DoD
- [ ] Picking `gapt_anthropic_sdk` in G.3's picker actually routes
      through the Anthropic SDK (visible: stream-json events come
      from the SDK adapter, not the CLI subprocess).
- [ ] Missing API key → session create returns a clear error
      pointing to G.2's settings card.

---

## Cross-cutting

- All four credential storage paths use the existing
  `SecretVault` (no new storage layer).
- All auth subprocesses inherit the env var hygiene we established
  in Phase B (token never logged, never in argv).
- Tests: integration via existing `httpx.MockTransport` pattern;
  no real `claude` binary required for unit-level coverage.
- i18n: new section `settings.llm_backends.*` in en + ko.

---

## v1 close

Phase G closes when all 5 cycles' DoD pass. The dogfooding gate
from [`m2_phase_d.md`](m2_phase_d.md) only makes sense after Phase G
because *Claude Code CLI auth is the dogfood path*.
