import { apiGet, apiPost } from "@/api/client";

export type WorkflowRunStatus =
  | "queued"
  | "in_progress"
  | "completed_success"
  | "completed_failure"
  | "completed_cancelled"
  | "completed_neutral"
  | "unknown";

export interface CiRun {
  id: number;
  name: string;
  head_branch: string;
  head_sha: string;
  status: WorkflowRunStatus;
  html_url: string;
}

export function listCiRuns(
  projectId: string,
  options: { branch?: string; limit?: number } = {},
): Promise<CiRun[]> {
  const params = new URLSearchParams();
  if (options.branch) params.set("branch", options.branch);
  if (options.limit) params.set("limit", String(options.limit));
  const qs = params.toString();
  return apiGet<CiRun[]>(`/api/projects/${projectId}/ci/runs${qs ? `?${qs}` : ""}`);
}

export interface CiLogResponse {
  run_id: number;
  log: string;
  truncated: boolean;
}

export function fetchCiRunLogs(projectId: string, runId: number): Promise<CiLogResponse> {
  return apiGet<CiLogResponse>(`/api/projects/${projectId}/ci/runs/${runId}/logs`);
}

export interface RerunResponse {
  run_id: number;
  failed_only: boolean;
}

export function rerunCiRun(
  projectId: string,
  runId: number,
  options: { failed_only?: boolean } = {},
): Promise<RerunResponse> {
  const qs = options.failed_only ? "?failed_only=true" : "";
  return apiPost<RerunResponse>(`/api/projects/${projectId}/ci/runs/${runId}/rerun${qs}`);
}
