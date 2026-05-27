import { apiDelete, apiFetch, apiGet, apiPost } from "@/api/client";

export type ServiceState = "starting" | "running" | "exited" | "failed" | "stopping";

export interface WorkspaceService {
  workspace_id: string;
  label: string;
  cmd: string;
  port: number | null;
  auto_port: number | null;
  pid: number | null;
  state: ServiceState;
  started_at: number;
  exited_at: number | null;
  exit_code: number | null;
  bound_url: string | null;
  bound_host: string | null;
  /** Worktree-relative log path — pass to file-tail SSE. */
  log_path: string;
}

export interface StartServiceInput {
  label: string;
  cmd: string;
  port?: number | null;
  env?: Record<string, string>;
}

export interface ExposeResponse {
  workspace_id: string;
  label: string;
  host: string;
  url: string;
  port: number;
}

export const listServices = (workspaceId: string) =>
  apiGet<WorkspaceService[]>(`/_gapt/api/workspaces/${workspaceId}/services`);

export const startService = (workspaceId: string, input: StartServiceInput) =>
  apiFetch<WorkspaceService>(`/_gapt/api/workspaces/${workspaceId}/services`, {
    method: "POST",
    json: input,
  });

export const stopService = (workspaceId: string, label: string) =>
  apiPost<WorkspaceService>(`/_gapt/api/workspaces/${workspaceId}/services/${label}/stop`);

export const restartService = (workspaceId: string, label: string) =>
  apiPost<WorkspaceService>(`/_gapt/api/workspaces/${workspaceId}/services/${label}/restart`);

export const deleteService = (workspaceId: string, label: string) =>
  apiDelete<void>(`/_gapt/api/workspaces/${workspaceId}/services/${label}`);

export const exposeService = (
  workspaceId: string,
  label: string,
  body?: { port?: number; upstream_host?: string },
) =>
  apiFetch<ExposeResponse>(`/_gapt/api/workspaces/${workspaceId}/services/${label}/expose`, {
    method: "POST",
    json: body ?? {},
  });

export const unexposeService = (workspaceId: string, label: string) =>
  apiDelete<void>(`/_gapt/api/workspaces/${workspaceId}/services/${label}/expose`);
