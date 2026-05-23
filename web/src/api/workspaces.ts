import { apiDelete, apiGet, apiPost } from "@/api/client";

export type WorkspaceStatus = "creating" | "running" | "paused" | "stopped" | "failed" | "archived";

export interface WorkspaceResponse {
  id: string;
  project_id: string;
  branch: string;
  worktree_path: string;
  sandbox_id: string | null;
  status: WorkspaceStatus;
  last_activity_at: string;
  created_at: string;
}

export interface CreateWorkspaceInput {
  branch: string;
  worktree_path?: string;
}

export const listWorkspaces = (projectId: string): Promise<WorkspaceResponse[]> =>
  apiGet<WorkspaceResponse[]>(`/api/projects/${projectId}/workspaces`);

export const createWorkspace = (
  projectId: string,
  input: CreateWorkspaceInput,
): Promise<WorkspaceResponse> =>
  apiPost<WorkspaceResponse>(`/api/projects/${projectId}/workspaces`, input);

export const getWorkspace = (workspaceId: string): Promise<WorkspaceResponse> =>
  apiGet<WorkspaceResponse>(`/api/workspaces/${workspaceId}`);

export const stopWorkspace = (workspaceId: string): Promise<WorkspaceResponse> =>
  apiPost<WorkspaceResponse>(`/api/workspaces/${workspaceId}/stop`);

export const startWorkspace = (workspaceId: string): Promise<WorkspaceResponse> =>
  apiPost<WorkspaceResponse>(`/api/workspaces/${workspaceId}/start`);

export const deleteWorkspace = (workspaceId: string): Promise<void> =>
  apiDelete<void>(`/api/workspaces/${workspaceId}`);
