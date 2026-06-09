import { apiDelete, apiGet, apiPost } from "@/api/client";

export type WorkspaceStatus = "creating" | "running" | "paused" | "stopped" | "failed" | "archived";

/** Phase N.5 — one entry in a workspace's repo selection. Mirrors
 *  the backend ``WorkspaceSelectionResponse``. ``repository_id`` is
 *  null only when the project's repo row was archived after the
 *  workspace was created (FK ondelete=SET NULL on the join table). */
export interface WorkspaceSelection {
  repository_id: string | null;
  subpath: string;
  display_name: string;
  branch: string;
  git_remote_url: string | null;
}

export interface WorkspaceResponse {
  id: string;
  project_id: string;
  /** Phase N.5 — workspace identity. Replaces the old ``branch``
   *  field; per-repo branches live on ``selections`` now. */
  name: string;
  worktree_path: string;
  sandbox_id: string | null;
  status: WorkspaceStatus;
  last_activity_at: string;
  created_at: string;
  selections: WorkspaceSelection[];
}

/** Phase N.5 — operator-supplied repo pick at create time. */
export interface WorkspaceRepoSelectionInput {
  repository_id: string;
  branch: string;
}

export interface CreateWorkspaceInput {
  name: string;
  /** Omit / null for "every project repo at its default_branch". */
  selections?: WorkspaceRepoSelectionInput[] | null;
  worktree_path?: string;
}

export const listWorkspaces = (projectId: string): Promise<WorkspaceResponse[]> =>
  apiGet<WorkspaceResponse[]>(`/_gapt/api/projects/${projectId}/workspaces`);

// Phase C.2.a — every non-archived workspace across every project.
export const listAllActiveWorkspaces = (): Promise<WorkspaceResponse[]> =>
  apiGet<WorkspaceResponse[]>(`/_gapt/api/workspaces`);

export const createWorkspace = (
  projectId: string,
  input: CreateWorkspaceInput,
): Promise<WorkspaceResponse> =>
  apiPost<WorkspaceResponse>(`/_gapt/api/projects/${projectId}/workspaces`, input);

export const getWorkspace = (workspaceId: string): Promise<WorkspaceResponse> =>
  apiGet<WorkspaceResponse>(`/_gapt/api/workspaces/${workspaceId}`);

export const stopWorkspace = (workspaceId: string): Promise<WorkspaceResponse> =>
  apiPost<WorkspaceResponse>(`/_gapt/api/workspaces/${workspaceId}/stop`);

export const startWorkspace = (workspaceId: string): Promise<WorkspaceResponse> =>
  apiPost<WorkspaceResponse>(`/_gapt/api/workspaces/${workspaceId}/start`);

export const deleteWorkspace = (workspaceId: string): Promise<void> =>
  apiDelete<void>(`/_gapt/api/workspaces/${workspaceId}`);

// Phase N.4 — re-clone any project repositories that aren't on disk
// in this workspace yet. Idempotent; returns the post-clone outcome
// so the UI can surface success vs partial-failure.
export interface RehydrateResponse {
  workspace: WorkspaceResponse;
  outcome: string;
  detail: string | null;
}

export const rehydrateWorkspace = (
  workspaceId: string,
): Promise<RehydrateResponse> =>
  apiPost<RehydrateResponse>(`/_gapt/api/workspaces/${workspaceId}/rehydrate`);

// Phase C.2.d — cap stats. `cap=null` means no cap configured.
export interface WorkspaceStats {
  active: number;
  cap: number | null;
}

export const getWorkspaceStats = (): Promise<WorkspaceStats> =>
  apiGet<WorkspaceStats>(`/_gapt/api/workspaces/stats`);

/** Fetch the live git-clone log tail (plain text). The runner streams
 * `git clone --progress` stdout/stderr to a file in the worktree so
 * the UI can poll it while status="creating". */
export async function getWorkspaceCloneLog(
  workspaceId: string,
  tailBytes = 16384,
): Promise<string> {
  const resp = await fetch(
    `/_gapt/api/workspaces/${workspaceId}/clone-log?tail_bytes=${tailBytes}`,
    { credentials: "include" },
  );
  if (!resp.ok) return "";
  return resp.text();
}
