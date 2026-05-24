import { apiGet } from "@/api/client";

export interface DiffFile {
  path: string;
  status: string;
  additions: number;
  deletions: number;
}

export interface WorkspaceDiff {
  files: DiffFile[];
  unified: string;
  truncated: boolean;
}

export const getWorkspaceDiff = (workspaceId: string) =>
  apiGet<WorkspaceDiff>(`/api/workspaces/${workspaceId}/diff`);
