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

/** Outcome of a fetch / pull / sync run. `actions` lists each
 * sub-operation that ran so the UI can render "Synced — fetched +
 * pulled 3 + pushed 2" with one round-trip. */
export interface GitSyncResponse {
  ok: boolean;
  actions: string[];
  ahead: number;
  behind: number;
  output: string;
  error: string | null;
}

export interface GitDiscardResponse {
  ok: boolean;
  discarded: string[];
  skipped: { path: string; reason: string }[];
}

export const getGitStatus = (wid: string) =>
  apiGet<GitStatusResponse>(`/_gapt/api/workspaces/${wid}/git/status`);

export const gitFetch = (wid: string) =>
  apiFetch<GitSyncResponse>(`/_gapt/api/workspaces/${wid}/git/fetch`, {
    method: "POST",
  });

export const gitPull = (wid: string) =>
  apiFetch<GitSyncResponse>(`/_gapt/api/workspaces/${wid}/git/pull`, {
    method: "POST",
  });

export const gitSync = (wid: string) =>
  apiFetch<GitSyncResponse>(`/_gapt/api/workspaces/${wid}/git/sync`, {
    method: "POST",
  });

export const gitDiscard = (wid: string, paths: string[]) =>
  apiFetch<GitDiscardResponse>(`/_gapt/api/workspaces/${wid}/git/discard`, {
    method: "POST",
    json: { paths },
  });

export const getGitDiff = (wid: string, path: string, staged = false) =>
  apiGet<GitDiffResponse>(
    `/_gapt/api/workspaces/${wid}/git/diff?path=${encodeURIComponent(path)}&staged=${staged}`,
  );

export const gitCommit = (
  wid: string,
  body: { message: string; paths?: string[] },
) =>
  apiFetch<GitCommitResponse>(`/_gapt/api/workspaces/${wid}/git/commit`, {
    method: "POST",
    json: body,
  });

export const gitPush = (
  wid: string,
  body: { branch?: string | null; force_with_lease?: boolean } = {},
) =>
  apiFetch<GitPushResponse>(`/_gapt/api/workspaces/${wid}/git/push`, {
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
  apiFetch<CreatePrResponse>(`/_gapt/api/workspaces/${wid}/git/create-pr`, {
    method: "POST",
    json: body,
  });
