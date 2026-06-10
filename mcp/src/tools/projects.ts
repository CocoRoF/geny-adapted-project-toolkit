import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { GaptClient } from "../client.js";
import { tool } from "../register.js";

export function registerProjectTools(server: McpServer, gapt: GaptClient): void {
  tool(
    server,
    "gapt_overview",
    "Start here. One-call snapshot of the GAPT instance: all projects (with repo counts), every active workspace, and the workspace capacity stats. Use before anything else to orient yourself.",
    {},
    async () => {
      const [projects, workspaces, stats] = await Promise.all([
        gapt.get("/_gapt/api/projects"),
        gapt.get("/_gapt/api/workspaces"),
        gapt.get("/_gapt/api/workspaces/stats"),
      ]);
      return { projects, active_workspaces: workspaces, workspace_stats: stats };
    },
  );

  tool(
    server,
    "list_projects",
    "List all projects with slug, display name, repository count and primary repo URL.",
    {},
    () => gapt.get("/_gapt/api/projects"),
  );

  tool(
    server,
    "get_project",
    "Full detail of one project: metadata + its repositories[] (ids, subpaths, remotes, default branches) + environments[]. The repository ids here are what git_* tools take as repo_id.",
    { project_id: z.string().describe("Project ULID") },
    async ({ project_id }) => {
      const [project, repositories, environments] = await Promise.all([
        gapt.get(`/_gapt/api/projects/${project_id}`),
        gapt.get(`/_gapt/api/projects/${project_id}/repositories`),
        gapt.get(`/_gapt/api/projects/${project_id}/environments`),
      ]);
      return { project, repositories, environments };
    },
  );

  tool(
    server,
    "create_project",
    "Create a project. Pass git_remote_url for the classic single-repo import, or omit it (empty string) for an EMPTY project you add repositories to afterwards via add_repository.",
    {
      slug: z
        .string()
        .regex(/^[a-z0-9](?:[-a-z0-9]{0,118}[a-z0-9])?$/)
        .describe("URL-safe unique slug, e.g. 'my-service'"),
      display_name: z.string().min(1).max(200),
      git_remote_url: z
        .string()
        .default("")
        .describe("https git URL; empty string = empty project (add repos later)"),
      git_provider: z.enum(["github", "gitlab", "bitbucket", "other"]).default("github"),
    },
    ({ slug, display_name, git_remote_url, git_provider }) =>
      gapt.post("/_gapt/api/projects", {
        slug,
        display_name,
        git_remote_url,
        git_provider,
      }),
  );

  tool(
    server,
    "list_repositories",
    "List a project's repositories: id (use as repo_id in git tools), subpath (folder inside workspaces), git_remote_url (null = empty/git-init candidate), default_branch.",
    { project_id: z.string() },
    ({ project_id }) => gapt.get(`/_gapt/api/projects/${project_id}/repositories`),
  );

  tool(
    server,
    "add_repository",
    "Attach another git repository to a project (multi-repo). subpath is the folder name inside future workspaces. Omit git_remote_url for an empty folder candidate. NOTE: repos added after a workspace was created don't auto-clone into it — use manage_workspace(action='rehydrate') or create a fresh workspace.",
    {
      project_id: z.string(),
      subpath: z
        .string()
        .regex(/^[A-Za-z0-9._-]{1,120}$/)
        .describe("single path segment, e.g. 'frontend'"),
      display_name: z.string().min(1).max(200),
      git_remote_url: z.string().optional().describe("omit for empty folder"),
      default_branch: z.string().optional(),
    },
    ({ project_id, ...body }) =>
      gapt.post(`/_gapt/api/projects/${project_id}/repositories`, body),
  );

  tool(
    server,
    "list_remote_branches",
    "Branches advertised by a repository's git remote (ls-remote, cached ~60s). Use to pick a branch for create_workspace selections.",
    {
      project_id: z.string(),
      repo_id: z.string().optional().describe("repository id; omit = project's legacy primary"),
      refresh: z.boolean().default(false).describe("bust the cache"),
    },
    ({ project_id, repo_id, refresh }) =>
      gapt.get(`/_gapt/api/projects/${project_id}/remote-branches`, {
        repo_id,
        refresh: refresh ? "true" : undefined,
      }),
  );
}
