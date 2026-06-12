import { apiGet, apiPost, apiDelete } from "@/api/client";
import type { GitProvider } from "@/api/projects";

/** Phase N.4 — one row per repository attached to a project.
 *
 * A project can carry zero or more of these. The IDE's source-control
 * tree lists them and lets the operator pick which one each git op
 * targets; the project list card surfaces "N repositories" when N > 1
 * so the multi-repo nature is obvious before opening the project. */
export interface ProjectRepository {
  id: string;
  project_id: string;
  /** Folder name under the workspace's worktree where this repo lives.
   *  Empty string = the legacy "this IS the worktree root" layout. */
  subpath: string;
  display_name: string;
  git_remote_url: string | null;
  git_provider: GitProvider | null;
  default_compose_paths: string[];
  compose_profile_dev: string | null;
  compose_profile_prod: string | null;
  default_branch: string | null;
  sort_order: number;
}

export interface RepositoryCreatePayload {
  subpath: string;
  display_name: string;
  git_remote_url?: string | null;
  git_provider?: GitProvider | null;
  default_branch?: string | null;
  sort_order?: number;
}

export const listProjectRepositories = (projectId: string) =>
  apiGet<ProjectRepository[]>(`/_gapt/api/projects/${projectId}/repositories`);

export const addProjectRepository = (projectId: string, body: RepositoryCreatePayload) =>
  apiPost<ProjectRepository>(`/_gapt/api/projects/${projectId}/repositories`, body);

export const archiveProjectRepository = (projectId: string, repoId: string) =>
  apiDelete<void>(`/_gapt/api/projects/${projectId}/repositories/${repoId}`);
