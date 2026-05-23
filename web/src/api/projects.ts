import { apiDelete, apiGet, apiPost } from "@/api/client";

export type GitProvider = "github" | "gitlab" | "bitbucket" | "other";

export interface ProjectResponse {
  id: string;
  org_id: string;
  owner_id: string;
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
  org_id: string;
  slug: string;
  display_name: string;
  git_remote_url: string;
  git_provider?: GitProvider;
  git_auth_secret_ref?: string;
  default_compose_paths?: string[];
  compose_profile_dev?: string;
  compose_profile_prod?: string;
}

export const listProjects = (orgId?: string): Promise<ProjectResponse[]> => {
  const q = orgId ? `?org_id=${encodeURIComponent(orgId)}` : "";
  return apiGet<ProjectResponse[]>(`/api/projects${q}`);
};

export const createProject = (input: CreateProjectInput): Promise<ProjectResponse> =>
  apiPost<ProjectResponse>("/api/projects", input);

export const getProject = (projectId: string): Promise<ProjectResponse> =>
  apiGet<ProjectResponse>(`/api/projects/${projectId}`);

export const archiveProject = (projectId: string): Promise<void> =>
  apiDelete<void>(`/api/projects/${projectId}`);
