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
  apiGet<EnvironmentResponse[]>(`/api/projects/${projectId}/environments`);

export const createEnvironment = (projectId: string, payload: EnvironmentPayload) =>
  apiFetch<EnvironmentResponse>(`/api/projects/${projectId}/environments`, {
    method: "POST",
    json: payload,
  });

export const updateEnvironment = (envId: string, payload: EnvironmentPayload) =>
  apiFetch<EnvironmentResponse>(`/api/environments/${envId}`, {
    method: "PUT",
    json: payload,
  });

export const deleteEnvironment = (envId: string) =>
  apiDelete<void>(`/api/environments/${envId}`);

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
  apiFetch<DeployResultResponse>(`/api/environments/${envId}/deploy`, {
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
  (async () => {
    try {
      const resp = await fetch(`/api/environments/${envId}/deploy/stream`, {
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
  apiFetch<AsyncDeployAccepted>(`/api/environments/${envId}/deploy/async`, {
    method: "POST",
    json: body,
  });

export const getActiveDeploy = (envId: string) =>
  apiGet<ActiveRun | null>(`/api/environments/${envId}/deploy/active`);

export const getDeployRun = (runId: string) =>
  apiGet<ActiveRun>(`/api/deploy/runs/${runId}`);

export const cancelDeployRun = (runId: string) =>
  apiPost<void>(`/api/deploy/runs/${runId}/cancel`);

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
  apiGet<RunDetail>(`/api/deploy/runs/${runId}/detail`);

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
  apiGet<StackStatus>(`/api/environments/${envId}/stack`);

export const stopStack = (envId: string) =>
  apiPost<StackOpResult>(`/api/environments/${envId}/stack/down`);

export const restartStack = (envId: string) =>
  apiPost<StackOpResult>(`/api/environments/${envId}/stack/restart`);

export const rerouteStack = (envId: string) =>
  apiPost<StackOpResult>(`/api/environments/${envId}/stack/reroute`);

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
  apiFetch<RollbackResultResponse>(`/api/environments/${envId}/rollback`, {
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
  apiGet<DeployRunRow[]>(`/api/environments/${envId}/runs?limit=${limit}`);
