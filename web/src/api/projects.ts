import { apiDelete, apiGet, apiPost } from "@/api/client";

export type GitProvider = "github" | "gitlab" | "bitbucket" | "other";

export interface ProjectResponse {
  id: string;
  slug: string;
  display_name: string;
  git_remote_url: string;
  git_provider: GitProvider;
  git_auth_secret_ref: string | null;
  default_compose_paths: string[];
  compose_profile_dev: string | null;
  compose_profile_prod: string | null;
  created_at: string;
  archived_at: string | null;
  /** Phase N.4 — count of active ProjectRepository rows. 0 = empty
   *  project (no git, scratch worktree), 1 = legacy single-repo,
   *  2+ = VS Code-style multi-root project. */
  repository_count: number;
  /** Primary repo URL — null only for empty projects. Same as
   *  `git_remote_url` for legacy single-repo projects. */
  primary_repository_url: string | null;
  /** Primary repo subpath. Empty string for legacy single-repo
   *  (the repo IS the worktree root). */
  primary_repository_subpath: string;
}

export interface CreateProjectInput {
  slug: string;
  display_name: string;
  git_remote_url: string;
  git_provider?: GitProvider;
  git_auth_secret_ref?: string;
  default_compose_paths?: string[];
  compose_profile_dev?: string;
  compose_profile_prod?: string;
}

export const listProjects = (): Promise<ProjectResponse[]> =>
  apiGet<ProjectResponse[]>("/_gapt/api/projects");

export const createProject = (input: CreateProjectInput): Promise<ProjectResponse> =>
  apiPost<ProjectResponse>("/_gapt/api/projects", input);

export const getProject = (projectId: string): Promise<ProjectResponse> =>
  apiGet<ProjectResponse>(`/_gapt/api/projects/${projectId}`);

export const archiveProject = (projectId: string): Promise<void> =>
  apiDelete<void>(`/_gapt/api/projects/${projectId}`);

export interface RemoteBranchesResponse {
  head: string | null;
  branches: string[];
}

export const getRemoteBranches = (
  projectId: string,
  opts: { refresh?: boolean; repoId?: string | null } = {},
): Promise<RemoteBranchesResponse> => {
  const params = new URLSearchParams();
  if (opts.refresh) params.set("refresh", "true");
  if (opts.repoId) params.set("repo_id", opts.repoId);
  const query = params.toString();
  return apiGet<RemoteBranchesResponse>(
    `/_gapt/api/projects/${projectId}/remote-branches${query ? `?${query}` : ""}`,
  );
};
