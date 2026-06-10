import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { GaptClient } from "../client.js";
import { tool } from "../register.js";

export function registerFileTools(server: McpServer, gapt: GaptClient): void {
  tool(
    server,
    "list_files",
    "List a directory inside the workspace. path is relative to /workspace ('' = root; 'geny/src' = inside the geny repo). Returns name/path/kind(file|dir)/size entries.",
    {
      workspace_id: z.string(),
      path: z.string().default("").describe("dir relative to /workspace; empty = root"),
    },
    ({ workspace_id, path }) =>
      gapt.get(`/_gapt/api/workspaces/${workspace_id}/tree`, { path }),
  );

  tool(
    server,
    "read_file",
    "Read one file from the workspace. Returns { path, encoding, text }. Binary files come back base64-encoded.",
    {
      workspace_id: z.string(),
      path: z.string().min(1).describe("file path relative to /workspace"),
    },
    ({ workspace_id, path }) =>
      gapt.get(`/_gapt/api/workspaces/${workspace_id}/file`, { path }),
  );

  tool(
    server,
    "write_file",
    "Create or overwrite one file in the workspace (parent dirs auto-created). Full-content write — read first, then write the whole new content.",
    {
      workspace_id: z.string(),
      path: z.string().min(1),
      content: z.string().max(2_000_000),
      encoding: z.enum(["utf-8", "base64"]).default("utf-8"),
    },
    ({ workspace_id, path, content, encoding }) =>
      gapt.request("PUT", `/_gapt/api/workspaces/${workspace_id}/file`, {
        query: { path },
        body: { content, encoding },
      }),
  );

  tool(
    server,
    "delete_path",
    "Delete a file (or empty directory) in the workspace. DESTRUCTIVE — confirm with the user unless they explicitly asked for the deletion.",
    {
      workspace_id: z.string(),
      path: z.string().min(1),
    },
    ({ workspace_id, path }) =>
      gapt.delete(`/_gapt/api/workspaces/${workspace_id}/file`, { path }),
  );

  tool(
    server,
    "git_diff_file",
    "Unified diff of ONE file vs HEAD (working tree by default, staged=true for the index side). Pass repo_id on multi-repo projects; path is relative to that repo's root.",
    {
      workspace_id: z.string(),
      path: z.string().min(1),
      repo_id: z.string().optional(),
      staged: z.boolean().default(false),
    },
    ({ workspace_id, path, repo_id, staged }) =>
      gapt.get(`/_gapt/api/workspaces/${workspace_id}/git/diff`, {
        path,
        repo_id,
        staged: staged ? "true" : undefined,
      }),
  );
}
