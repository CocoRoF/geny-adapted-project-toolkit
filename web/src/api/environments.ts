import { apiDelete, apiFetch, apiGet } from "@/api/client";

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
