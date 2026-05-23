import { apiGet } from "@/api/client";

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
