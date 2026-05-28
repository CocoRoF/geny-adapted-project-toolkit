# M2 Phase E — Resource model integrity

> Parent: [`m2_m5_outline.md`](m2_m5_outline.md) · [`00_master_plan.md`](00_master_plan.md)
>
> Phase E was added 2026-05-28 after Grafana was removed. Three
> observations made it load-bearing for v1 close:
>
> 1. **Prometheus has no consumer** after Grafana removal — data is
>    collected but nobody reads it.
> 2. **GPU is "wall art"** — performance tab shows host GPU stats,
>    but workspace containers never receive `--gpus`, so agents
>    inside can't actually use CUDA.
> 3. **GPU sampling is one-shot**, not live — performance tab does
>    a single `nvidia-smi` fetch, not part of the SSE stream.

The three cycles below close all three gaps without expanding scope
beyond v1's single-admin model.

---

## E.1 — GPU passthrough into workspace containers

Today: [workspace_sandbox/manager.py:244](server/src/gapt_server/domains/workspace_sandbox/manager.py#L244)
assembles `docker run` argv with no GPU flags. The agent inside
`gapt-ws-<wid>` cannot call CUDA.

### Design

- New `Settings.workspace_gpus: str | None = None`. Recognised values:
  - `None` (default): no GPU mapped — `docker run` argv unchanged.
  - `"all"`: maps every host GPU into the container.
  - `"0"`, `"1"`, `"0,1"`: comma-separated indices (passed through
    to docker as `--gpus device=0,1`).
- `WorkspaceSandbox.gpus: str | None = None` — set by the manager
  at handle creation from the setting; takes effect on the next
  `ensure()` (i.e. next container boot).
- `ensure()` adds `--gpus <spec>` to argv when `self.gpus` is set.
- **Single-admin scope decision**: one GPU policy for the whole
  install. Per-workspace override deferred — requires a UI we
  don't have time to build before v1 close, and the use case
  (some workspaces GPU, others not) isn't real yet.
- **Host requirement**: NVIDIA Container Toolkit installed. Without
  it `docker run --gpus` fails loud — we don't silently degrade
  (an agent thinking it has GPU but seeing none would be worse).

### DoD

- [ ] `GAPT_WORKSPACE_GPUS=all` boots a workspace whose container
      sees the host GPUs via `nvidia-smi`.
- [ ] Unset setting → argv contains NO `--gpus` flag (regression
      check against accidentally always-on GPU).
- [ ] `GAPT_WORKSPACE_GPUS=0` maps GPU 0 only.
- [ ] Settings UI surfaces the current value + provides a way to
      change it.

---

## E.2 — Performance tab + Prometheus mashup

Today: two parallel data paths describe the same containers. The
performance tab uses `docker stats` directly; Prometheus stores the
app-level counters that NOBODY reads after Grafana left.

Phase E.2 promotes Prometheus' data to a **first-class consumer
inside GAPT** — the performance tab — so the counter is no longer
orphaned.

### Design

- The `MetricsRegistry` Phase B already wires (and Prometheus
  scrapes) is the source of truth for per-project token / cost /
  session counters. Performance tab reads it **directly** (no
  Prometheus dependency — Prometheus is just a side door for
  external scrapers).
- `ContainerSummary` (the SSE payload) gains:
  - `session_metrics: { cost_usd_total, input_tokens_total,
    output_tokens_total } | null` — joined from the agent session
    whose `workspace_id` matches this container. `null` for
    infra containers (caddy / postgres / …).
- SSE stream also folds in **live GPU samples** every tick
  (currently 2s). Removes the 1-shot `/gpu` fetch.

### Out-of-scope (deferred)

- Per-session timeline graphs in the perf tab — `ContainerSampler`
  doesn't keep historical samples today. The Phase D.3 session_events
  table is the right substrate for that, but the UI work is v1.5+.

### DoD

- [ ] SSE payload includes `gpus` (array) + each container's
      `session_metrics` (or null).
- [ ] UI: GPU tiles update in real time without a separate fetch.
- [ ] UI: Container card expansion shows cost / token totals when
      the row belongs to an active agent session.
- [ ] Performance tab still loads on a CPU-only host (no GPU section
      renders).

---

## E.3 — Prometheus profile-gate

Today: dev compose runs Prometheus always-on at port 39090. The
metrics profile in prod gates it correctly already.

After E.2, the **internal consumer** of metric values is the
performance tab reading the registry directly. Prometheus stays as
an **external** integration point (curl/promtool/your own viz).

### Design

- Move dev Prometheus behind `profiles: ["metrics"]` (matches prod).
- Default dev boot no longer includes Prometheus.
- `/metrics` endpoint on the server stays — costs nothing to expose
  and is the standard for external scrapers.
- Document in `dev_setup.md`: how to bring up Prometheus when needed.

### DoD

- [ ] `docker compose -f docker-compose.dev.yml up -d` (no profile)
      does NOT start Prometheus.
- [ ] `docker compose -f docker-compose.dev.yml --profile metrics
      up -d prometheus` DOES start it.
- [ ] `curl localhost:38001/metrics` still works regardless.

---

## DoD summary (Phase E close)

After E.1 + E.2 + E.3:

- GPU policy is set in one place (Settings), enforced via
  `docker run --gpus`, validated by agent code that runs CUDA.
- Performance tab is the single pane: live container stats + live
  GPU + per-container agent cost/tokens.
- Prometheus is opt-in plumbing for external scrapers; internal
  data path is registry-direct.

Phase E close → v1 ready for the dogfooding gate from
[`m2_phase_d.md`](m2_phase_d.md).
