import { apiFetch, apiGet } from "@/api/client";

/** Phase N.4 — Append `?repo_id=<id>` to a path when the caller
 * targets a specific repository within a multi-repo project. Omit
 * the query (caller passes undefined) to fall back to the project's
 * primary repository server-side.
 *
 * The signature deliberately preserves the existing single-arg call
 * sites of every git endpoint — `repoId` is the optional second
 * positional in each wrapper, so pre-N.4 callers compile unchanged. */
function withRepo(path: string, repoId?: string | null): string {
  if (!repoId) return path;
  return `${path}${path.includes("?") ? "&" : "?"}repo_id=${encodeURIComponent(repoId)}`;
}

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

export const getGitStatus = (wid: string, repoId?: string | null) =>
  apiGet<GitStatusResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/status`, repoId));

export const gitFetch = (wid: string, repoId?: string | null) =>
  apiFetch<GitSyncResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/fetch`, repoId), {
    method: "POST",
  });

export const gitPull = (wid: string, repoId?: string | null) =>
  apiFetch<GitSyncResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/pull`, repoId), {
    method: "POST",
  });

export const gitSync = (wid: string, repoId?: string | null) =>
  apiFetch<GitSyncResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/sync`, repoId), {
    method: "POST",
  });

export const gitDiscard = (
  wid: string,
  paths: string[],
  repoId?: string | null,
  opts?: { includeStaged?: boolean },
) =>
  apiFetch<GitDiscardResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/discard`, repoId), {
    method: "POST",
    json: { paths, ...(opts?.includeStaged ? { include_staged: true } : {}) },
  });

// ─── branches ───────────────────────────────────────────────────────

export interface GitBranchInfo {
  name: string;
  // Known values: "local" | "remote" — kept open (plain string)
  // because the backend may grow kinds without a lockstep UI release.
  kind: string;
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

export const getGitBranches = (wid: string, repoId?: string | null) =>
  apiGet<GitBranchesResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/branches`, repoId));

export const gitCheckout = (
  wid: string,
  body: { branch: string; create?: boolean; start_point?: string | null; force?: boolean },
  repoId?: string | null,
) =>
  apiFetch<GitCheckoutResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/checkout`, repoId), {
    method: "POST",
    json: body,
  });

export const gitBranchDelete = (
  wid: string,
  body: { branch: string; force?: boolean },
  repoId?: string | null,
) =>
  apiFetch<GitCheckoutResponse>(
    withRepo(`/_gapt/api/workspaces/${wid}/git/branch/delete`, repoId),
    { method: "POST", json: body },
  );

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

export const getGitStashList = (wid: string, repoId?: string | null) =>
  apiGet<GitStashListResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/stash/list`, repoId));

export const gitStashPush = (
  wid: string,
  body: { message?: string; include_untracked?: boolean } = {},
  repoId?: string | null,
) =>
  apiFetch<GitStashOpResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/stash/push`, repoId), {
    method: "POST",
    json: body,
  });

export const gitStashPop = (wid: string, body: { ref?: string } = {}, repoId?: string | null) =>
  apiFetch<GitStashOpResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/stash/pop`, repoId), {
    method: "POST",
    json: body,
  });

export const gitStashDrop = (wid: string, body: { ref?: string } = {}, repoId?: string | null) =>
  apiFetch<GitStashOpResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/stash/drop`, repoId), {
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
  repoId?: string | null,
) => {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.all_branches !== undefined) params.set("all_branches", String(opts.all_branches));
  if (repoId) params.set("repo_id", repoId);
  const qs = params.toString();
  return apiGet<GitLogResponse>(`/_gapt/api/workspaces/${wid}/git/log${qs ? `?${qs}` : ""}`);
};

export const getGitDiff = (wid: string, path: string, staged = false, repoId?: string | null) =>
  apiGet<GitDiffResponse>(
    withRepo(
      `/_gapt/api/workspaces/${wid}/git/diff?path=${encodeURIComponent(path)}&staged=${staged}`,
      repoId,
    ),
  );

export const gitCommit = (
  wid: string,
  body: { message: string; paths?: string[] },
  repoId?: string | null,
) =>
  apiFetch<GitCommitResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/commit`, repoId), {
    method: "POST",
    json: body,
  });

export const gitPush = (
  wid: string,
  body: { branch?: string | null; force_with_lease?: boolean } = {},
  repoId?: string | null,
) =>
  apiFetch<GitPushResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/push`, repoId), {
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
  repoId?: string | null,
) =>
  apiFetch<CreatePrResponse>(withRepo(`/_gapt/api/workspaces/${wid}/git/create-pr`, repoId), {
    method: "POST",
    json: body,
  });
