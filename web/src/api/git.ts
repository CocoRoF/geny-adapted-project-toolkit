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

// ─── branches ───────────────────────────────────────────────────────

export interface GitBranchInfo {
  name: string;
  kind: "local" | "remote" | string;
  current: boolean;
  upstream: string | null;
  ahead: number | null;
  behind: number | null;
  last_commit_sha: string | null;
  last_commit_subject: string | null;
}

export interface GitBranchesResponse {
  current: string | null;
  branches: GitBranchInfo[];
}

export interface GitCheckoutResponse {
  ok: boolean;
  branch: string;
  output: string;
  error: string | null;
}

export const getGitBranches = (wid: string) =>
  apiGet<GitBranchesResponse>(`/_gapt/api/workspaces/${wid}/git/branches`);

export const gitCheckout = (
  wid: string,
  body: { branch: string; create?: boolean; start_point?: string | null; force?: boolean },
) =>
  apiFetch<GitCheckoutResponse>(`/_gapt/api/workspaces/${wid}/git/checkout`, {
    method: "POST",
    json: body,
  });

export const gitBranchDelete = (
  wid: string,
  body: { branch: string; force?: boolean },
) =>
  apiFetch<GitCheckoutResponse>(`/_gapt/api/workspaces/${wid}/git/branch/delete`, {
    method: "POST",
    json: body,
  });

// ─── stash ──────────────────────────────────────────────────────────

export interface GitStashEntry {
  ref: string;
  branch: string | null;
  subject: string;
  age_seconds: number | null;
}

export interface GitStashListResponse {
  entries: GitStashEntry[];
}

export interface GitStashOpResponse {
  ok: boolean;
  output: string;
  error: string | null;
}

export const getGitStashList = (wid: string) =>
  apiGet<GitStashListResponse>(`/_gapt/api/workspaces/${wid}/git/stash/list`);

export const gitStashPush = (
  wid: string,
  body: { message?: string; include_untracked?: boolean } = {},
) =>
  apiFetch<GitStashOpResponse>(`/_gapt/api/workspaces/${wid}/git/stash/push`, {
    method: "POST",
    json: body,
  });

export const gitStashPop = (wid: string, body: { ref?: string } = {}) =>
  apiFetch<GitStashOpResponse>(`/_gapt/api/workspaces/${wid}/git/stash/pop`, {
    method: "POST",
    json: body,
  });

export const gitStashDrop = (wid: string, body: { ref?: string } = {}) =>
  apiFetch<GitStashOpResponse>(`/_gapt/api/workspaces/${wid}/git/stash/drop`, {
    method: "POST",
    json: body,
  });

// ─── commit log (graph) ─────────────────────────────────────────────

export interface GitLogCommit {
  sha: string;
  short_sha: string;
  parents: string[];
  author: string;
  author_email: string;
  iso_date: string;
  subject: string;
  refs: string[];
}

export interface GitLogResponse {
  commits: GitLogCommit[];
}

export const getGitLog = (
  wid: string,
  opts: { limit?: number; all_branches?: boolean } = {},
) => {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.all_branches !== undefined)
    params.set("all_branches", String(opts.all_branches));
  const qs = params.toString();
  return apiGet<GitLogResponse>(
    `/_gapt/api/workspaces/${wid}/git/log${qs ? `?${qs}` : ""}`,
  );
};

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
