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
  opts: { refresh?: boolean } = {},
): Promise<RemoteBranchesResponse> => {
  const query = opts.refresh ? "?refresh=true" : "";
  return apiGet<RemoteBranchesResponse>(
    `/_gapt/api/projects/${projectId}/remote-branches${query}`,
  );
};
