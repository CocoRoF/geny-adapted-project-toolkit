import { apiFetch, apiGet } from "@/api/client";

export interface GitStatusEntry {
  path: string;
  /** Porcelain `XY` (e.g. " M", "M ", "??", "A "). */
  status: string;
}

export interface GitStatusResponse {
  branch: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  entries: GitStatusEntry[];
  recent_commits: { sha: string; message: string }[];
}

export interface GitDiffResponse {
  path: string;
  diff: string;
}

export interface GitCommitResponse {
  sha: string;
  branch: string | null;
  log: string;
}

export interface GitPushResponse {
  branch: string | null;
  log: string;
}

export interface CreatePrResponse {
  url: string;
  number: number;
  log: string;
}

export const getGitStatus = (wid: string) =>
  apiGet<GitStatusResponse>(`/api/workspaces/${wid}/git/status`);

export const getGitDiff = (wid: string, path: string, staged = false) =>
  apiGet<GitDiffResponse>(
    `/api/workspaces/${wid}/git/diff?path=${encodeURIComponent(path)}&staged=${staged}`,
  );

export const gitCommit = (
  wid: string,
  body: { message: string; paths?: string[] },
) =>
  apiFetch<GitCommitResponse>(`/api/workspaces/${wid}/git/commit`, {
    method: "POST",
    json: body,
  });

export const gitPush = (
  wid: string,
  body: { branch?: string | null; force_with_lease?: boolean } = {},
) =>
  apiFetch<GitPushResponse>(`/api/workspaces/${wid}/git/push`, {
    method: "POST",
    json: body,
  });

export const createPr = (
  wid: string,
  body: {
    title: string;
    body?: string;
    base?: string;
    head?: string | null;
    draft?: boolean;
  },
) =>
  apiFetch<CreatePrResponse>(`/api/workspaces/${wid}/git/create-pr`, {
    method: "POST",
    json: body,
  });
