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

export interface EnvironmentResponse extends EnvironmentPayload {
  id: string;
  project_id: string;
  created_at: string;
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
}

export const triggerDeploy = (envId: string, body: DeployRequestBody) =>
  apiFetch<DeployResultResponse>(`/api/environments/${envId}/deploy`, {
    method: "POST",
    json: body,
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
  apiFetch<RollbackResultResponse>(`/api/environments/${envId}/rollback`, {
    method: "POST",
    json: body,
  });
