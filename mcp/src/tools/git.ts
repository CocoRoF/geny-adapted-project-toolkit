import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { GaptClient } from "../client.js";
import { tool } from "../register.js";

/** All git tools take an optional repo_id. On multi-repo projects you
 * MUST pass it (get ids from list_repositories) — omitting falls back
 * to the project's primary repository. */
export function registerGitTools(server: McpServer, gapt: GaptClient): void {
  const repoArg = {
    workspace_id: z.string(),
    repo_id: z
      .string()
      .optional()
      .describe("ProjectRepository id — REQUIRED in spirit on multi-repo projects; omit = primary repo"),
  };

  tool(
    server,
    "git_status",
    "Branch, upstream, ahead/behind counts, dirty files (porcelain codes) and recent commits for one repo of the workspace. 412 git.repo_not_cloned → run manage_workspace(action='rehydrate').",
    repoArg,
    ({ workspace_id, repo_id }) =>
      gapt.get(`/_gapt/api/workspaces/${workspace_id}/git/status`, { repo_id }),
  );

  tool(
    server,
    "git_log",
    "Commit history (sha, subject, author, refs, parents). all_branches=true shows the whole graph, not just HEAD's first-parent chain.",
    {
      ...repoArg,
      limit: z.number().int().min(1).max(200).default(30),
      all_branches: z.boolean().default(false),
    },
    ({ workspace_id, repo_id, limit, all_branches }) =>
      gapt.get(`/_gapt/api/workspaces/${workspace_id}/git/log`, {
        repo_id,
        limit,
        all_branches: all_branches ? "true" : undefined,
      }),
  );

  tool(
    server,
    "git_branches",
    "Local + remote branches with upstream tracking and ahead/behind per branch.",
    repoArg,
    ({ workspace_id, repo_id }) =>
      gapt.get(`/_gapt/api/workspaces/${workspace_id}/git/branches`, { repo_id }),
  );

  tool(
    server,
    "git_checkout",
    "Switch branches. create=true makes a new branch (optionally from start_point, e.g. 'origin/main'). Checking out a remote branch: pass the local name + create=true + start_point='origin/<name>'.",
    {
      ...repoArg,
      branch: z.string().min(1),
      create: z.boolean().default(false),
      start_point: z.string().optional(),
      force: z.boolean().default(false).describe("discard local changes blocking the switch — destructive"),
    },
    ({ workspace_id, repo_id, branch, create, start_point, force }) =>
      gapt.post(
        `/_gapt/api/workspaces/${workspace_id}/git/checkout`,
        { branch, create, start_point: start_point ?? null, force },
        { repo_id },
      ),
  );

  tool(
    server,
    "git_commit",
    "Stage the given paths (or everything dirty when paths omitted) and commit with the message. Returns the new sha.",
    {
      ...repoArg,
      message: z.string().min(1).max(4000),
      paths: z.array(z.string()).optional().describe("repo-relative paths; omit = all changes"),
    },
    ({ workspace_id, repo_id, message, paths }) =>
      gapt.post(
        `/_gapt/api/workspaces/${workspace_id}/git/commit`,
        { message, ...(paths ? { paths } : {}) },
        { repo_id },
      ),
  );

  tool(
    server,
    "git_sync",
    "Remote sync, one tool four modes: 'fetch' (update remote refs only), 'pull' (ff-only), 'push' (current branch; first push auto-sets upstream), 'sync' (fetch→pull→push, the everyday catch-up). Auth uses the repo/project/system GitHub token chain configured in GAPT.",
    {
      ...repoArg,
      mode: z.enum(["fetch", "pull", "push", "sync"]).default("sync"),
      force_with_lease: z.boolean().default(false).describe("push only — force-with-lease after a rebase"),
    },
    ({ workspace_id, repo_id, mode, force_with_lease }) => {
      const base = `/_gapt/api/workspaces/${workspace_id}/git`;
      switch (mode) {
        case "fetch":
          return gapt.post(`${base}/fetch`, undefined, { repo_id });
        case "pull":
          return gapt.post(`${base}/pull`, undefined, { repo_id });
        case "push":
          return gapt.post(`${base}/push`, { force_with_lease }, { repo_id });
        case "sync":
          return gapt.post(`${base}/sync`, undefined, { repo_id });
      }
    },
  );

  tool(
    server,
    "git_discard",
    "Throw away working-tree changes to the given paths (tracked: restore; untracked: clean). DESTRUCTIVE and unrecoverable — confirm with the user first.",
    {
      ...repoArg,
      paths: z.array(z.string()).min(1),
    },
    ({ workspace_id, repo_id, paths }) =>
      gapt.post(`/_gapt/api/workspaces/${workspace_id}/git/discard`, { paths }, { repo_id }),
  );

  tool(
    server,
    "git_stash",
    "Stash operations: 'list', 'push' (save dirty state, untracked included; optional message), 'pop' (restore; optional ref like 'stash@{1}'), 'drop' (delete a ref — destructive).",
    {
      ...repoArg,
      action: z.enum(["list", "push", "pop", "drop"]),
      message: z.string().optional().describe("push only"),
      ref: z.string().optional().describe("pop/drop — e.g. 'stash@{0}'; pop defaults to latest"),
    },
    ({ workspace_id, repo_id, action, message, ref }) => {
      const base = `/_gapt/api/workspaces/${workspace_id}/git/stash`;
      switch (action) {
        case "list":
          return gapt.get(`${base}/list`, { repo_id });
        case "push":
          return gapt.post(`${base}/push`, { message, include_untracked: true }, { repo_id });
        case "pop":
          return gapt.post(`${base}/pop`, { ref }, { repo_id });
        case "drop":
          return gapt.post(`${base}/drop`, { ref }, { repo_id });
      }
    },
  );

  tool(
    server,
    "create_pr",
    "Open a GitHub pull request from the workspace's current branch of this repo. Pushes first if needed. Returns the PR number + URL.",
    {
      ...repoArg,
      title: z.string().min(1).max(300),
      body: z.string().max(20_000).optional(),
      base: z.string().default("main"),
      draft: z.boolean().default(false),
    },
    ({ workspace_id, repo_id, title, body, base, draft }) =>
      gapt.post(
        `/_gapt/api/workspaces/${workspace_id}/git/create-pr`,
        { title, body: body ?? "", base, draft },
        { repo_id },
      ),
  );
}
