import { apiDelete, apiFetch, apiGet, apiPost } from "@/api/client";

export type DeployTargetKind = "local" | "remote_ssh" | "webhook" | "k8s";

export interface EnvironmentPayload {
  name: string;
  deploy_target_kind: DeployTargetKind;
  deploy_target_config: Record<string, unknown>;
  require_2fa?: boolean;
  secret_refs?: string[];
  cost_multiplier?: number;
  hooks?: Record<string, unknown>;
}

export interface EnvironmentLastRun {
  run_id?: string;
  status?: string;
  bound_url?: string | null;
  version?: string;
  deployed_at?: string;
}

export interface EnvironmentResponse extends EnvironmentPayload {
  id: string;
  project_id: string;
  created_at: string;
  last_run?: EnvironmentLastRun;
}

export const listEnvironments = (projectId: string) =>
  apiGet<EnvironmentResponse[]>(`/_gapt/api/projects/${projectId}/environments`);

export const createEnvironment = (projectId: string, payload: EnvironmentPayload) =>
  apiFetch<EnvironmentResponse>(`/_gapt/api/projects/${projectId}/environments`, {
    method: "POST",
    json: payload,
  });

export const updateEnvironment = (envId: string, payload: EnvironmentPayload) =>
  apiFetch<EnvironmentResponse>(`/_gapt/api/environments/${envId}`, {
    method: "PUT",
    json: payload,
  });

export const deleteEnvironment = (envId: string) =>
  apiDelete<void>(`/_gapt/api/environments/${envId}`);

// ─────────────────────────── Deploy + rollback triggers ──

export interface DeployRequestBody {
  version?: string;
  two_factor_code?: string | null;
  target_options?: Record<string, unknown>;
}

export interface DeployResultResponse {
  run_id: string;
  status: string;
  exec_code?: string | null;
  log: string;
  bound_url?: string | null;
}

export const triggerDeploy = (envId: string, body: DeployRequestBody) =>
  apiFetch<DeployResultResponse>(`/_gapt/api/environments/${envId}/deploy`, {
    method: "POST",
    json: body,
  });

/** SSE wrapper around POST /deploy/stream. Returns an AbortController
 * the caller can use to cancel mid-flight; pushes parsed frames to
 * `onFrame` until the stream closes. */
export interface DeployStreamFrame {
  type: "log" | "status" | "done";
  content?: string;
  status?: string;
  result?: DeployResultResponse;
}

export function streamDeploy(
  envId: string,
  body: DeployRequestBody,
  onFrame: (frame: DeployStreamFrame) => void,
): AbortController {
  const ctrl = new AbortController();
  void (async () => {
    try {
      const resp = await fetch(`/_gapt/api/environments/${envId}/deploy/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
        credentials: "include",
      });
      if (!resp.ok || !resp.body) {
        onFrame({
          type: "done",
          result: {
            run_id: "",
            status: "failed",
            log: `HTTP ${resp.status}: ${await resp.text().catch(() => "")}`,
            bound_url: null,
          },
        });
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const raw = line.slice(5).trim();
            if (!raw) continue;
            try {
              onFrame(JSON.parse(raw) as DeployStreamFrame);
            } catch {
              // Malformed frame — skip silently.
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      onFrame({
        type: "done",
        result: {
          run_id: "",
          status: "failed",
          log: String(err),
          bound_url: null,
        },
      });
    }
  })();
  return ctrl;
}

// ──────────────────────────── Async / persistent deploy v2 ──
//
// `triggerDeployAsync()` returns immediately with the new run_id;
// the backend keeps the orchestrator task alive across HTTP
// connections. `getActiveRun()` lets a fresh tab discover whether
// a deploy is already in flight (auto-resume on remount).

export interface AsyncDeployAccepted {
  run_id: string;
  environment_id: string;
  status: string;
  started_at: string;
}

export interface ActiveRun {
  run_id: string;
  environment_id: string;
  project_id: string;
  version: string;
  status: string; // pending | running | success | failed | aborted
  started_at: string;
  bound_url: string | null;
  exec_code: string | null;
  finished_at: string | null;
}

export const triggerDeployAsync = (envId: string, body: DeployRequestBody) =>
  apiFetch<AsyncDeployAccepted>(`/_gapt/api/environments/${envId}/deploy/async`, {
    method: "POST",
    json: body,
  });

export const getActiveDeploy = (envId: string) =>
  apiGet<ActiveRun | null>(`/_gapt/api/environments/${envId}/deploy/active`);

export const getDeployRun = (runId: string) => apiGet<ActiveRun>(`/_gapt/api/deploy/runs/${runId}`);

export const cancelDeployRun = (runId: string) =>
  apiPost<void>(`/_gapt/api/deploy/runs/${runId}/cancel`);

// ───────────────────── Persistent run detail (DB-backed) ──

export interface EnvConfigSnapshot {
  id: string;
  name: string;
  deploy_target_kind: string;
  deploy_target_config: Record<string, unknown>;
  require_2fa: boolean;
  secret_refs: string[];
  cost_multiplier: number;
}

export interface ProjectSnapshot {
  id: string;
  slug: string;
  display_name: string;
}

export interface RunDetail {
  run: DeployRunRow;
  environment: EnvConfigSnapshot;
  project: ProjectSnapshot;
}

export const getDeployRunDetail = (runId: string) =>
  apiGet<RunDetail>(`/_gapt/api/deploy/runs/${runId}/detail`);

// ───────────────────── Stack lifecycle (post-deploy) ──

export interface StackService {
  container_id: string;
  container_name: string;
  service: string;
  image: string;
  status: string;
  health: string | null;
  started_at: string | null;
  exit_code: number | null;
}

export interface StackStatus {
  environment_id: string;
  project: string;
  services: StackService[];
  running_count: number;
  total_count: number;
}

export interface StackOpResult {
  environment_id: string;
  project: string;
  action: string;
  ok: boolean;
  affected: number;
  output: string;
}

export const getStackStatus = (envId: string) =>
  apiGet<StackStatus>(`/_gapt/api/environments/${envId}/stack`);

export interface StackLogs {
  environment_id: string;
  project: string;
  output: string;
  bytes: number;
}

export const getStackLogs = (envId: string, options: { tail?: number; since?: string } = {}) => {
  const q = new URLSearchParams();
  if (options.tail !== undefined) q.set("tail", String(options.tail));
  if (options.since) q.set("since", options.since);
  const suffix = q.toString();
  return apiGet<StackLogs>(
    `/_gapt/api/environments/${envId}/stack/logs${suffix ? `?${suffix}` : ""}`,
  );
};

export const stopStack = (envId: string) =>
  apiPost<StackOpResult>(`/_gapt/api/environments/${envId}/stack/down`);

export const restartStack = (envId: string) =>
  apiPost<StackOpResult>(`/_gapt/api/environments/${envId}/stack/restart`);

export interface StackRerouteBody {
  primary_service?: string | null;
  primary_port?: number | null;
  strip_prefix?: boolean | null;
  upstream_scheme?: "http" | "https" | null;
  upstream_host_header?: string | null;
  upstream_tls_insecure?: boolean | null;
  preview_mode?: "path" | "subdomain" | null;
}

export const rerouteStack = (envId: string, body?: StackRerouteBody) =>
  apiFetch<StackOpResult>(`/_gapt/api/environments/${envId}/stack/reroute`, {
    method: "POST",
    json: body ?? {},
  });

export interface RollbackRequestBody {
  run_id: string;
  to_version: string;
  two_factor_code?: string | null;
  target_options?: Record<string, unknown>;
}

export interface RollbackResultResponse {
  run_id: string;
  status: string;
  restored_version?: string | null;
  exec_code?: string | null;
  log: string;
}

export const triggerRollback = (envId: string, body: RollbackRequestBody) =>
  apiFetch<RollbackResultResponse>(`/_gapt/api/environments/${envId}/rollback`, {
    method: "POST",
    json: body,
  });

// ─────────────────────────── Deploy history ──

export interface DeployRunRow {
  id: string;
  environment_id: string;
  version: string;
  status: string;
  bound_url: string | null;
  exec_code: string | null;
  log_tail: string;
  started_at: string;
  finished_at: string | null;
  actor_id: string | null;
  trigger_kind: string;
}

export const listDeployRuns = (envId: string, limit = 20) =>
  apiGet<DeployRunRow[]>(`/_gapt/api/environments/${envId}/runs?limit=${limit}`);

/** Diagnostic for subdomain mode prereqs — DNS, Caddy admin, env,
 * Cloudflare provider state. */
export interface SubdomainDiagnose {
  preview_domain: string | null;
  sample_host: string;
  dns_resolves: boolean;
  dns_message: string;
  caddy_admin_reachable: boolean;
  caddy_has_wildcard_server: boolean;
  e2e_reachable: boolean;
  e2e_message: string;
  provider_configured: boolean;
  provider_account_id: string | null;
  provider_zone_id: string | null;
  provider_tunnel_id: string | null;
  tunnel_mode: "remote_managed" | "local_config" | "unknown" | null;
  tunnel_has_wildcard: boolean;
  next_steps: string[];
}

export const diagnoseSubdomainMode = () => apiGet<SubdomainDiagnose>(`/_gapt/api/preview/diagnose`);
