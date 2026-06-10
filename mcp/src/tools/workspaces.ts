import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { GaptClient } from "../client.js";
import { tool } from "../register.js";

export function registerWorkspaceTools(server: McpServer, gapt: GaptClient): void {
  tool(
    server,
    "list_workspaces",
    "List a project's workspaces (name, status, per-repo selections with branches).",
    { project_id: z.string() },
    ({ project_id }) => gapt.get(`/_gapt/api/projects/${project_id}/workspaces`),
  );

  tool(
    server,
    "create_workspace",
    "Create a named sandbox workspace. selections picks WHICH project repositories get cloned and at what branch each — omit selections to clone every repo at its default branch. Idempotent on (project, name): re-creating a live name returns the existing workspace. Clone is async — poll get_workspace until status='running'.",
    {
      project_id: z.string(),
      name: z.string().min(1).max(255).describe("workspace name, unique among live workspaces of the project"),
      selections: z
        .array(
          z.object({
            repository_id: z.string(),
            branch: z.string().default("").describe("branch to clone; empty = repo default / remote HEAD"),
          }),
        )
        .optional()
        .describe("omit = all repos at default branches"),
    },
    ({ project_id, name, selections }) =>
      gapt.post(`/_gapt/api/projects/${project_id}/workspaces`, {
        name,
        ...(selections ? { selections } : {}),
      }),
  );

  tool(
    server,
    "get_workspace",
    "Workspace detail: status (creating|running|stopped|failed|archived), worktree path, per-repo selections. Includes the git clone log tail while status='creating' so you can see progress / diagnose failures.",
    { workspace_id: z.string() },
    async ({ workspace_id }) => {
      const ws = await gapt.get<{ status?: string }>(`/_gapt/api/workspaces/${workspace_id}`);
      let clone_log: string | undefined;
      if (ws.status === "creating" || ws.status === "failed") {
        clone_log = await gapt
          .get<string>(`/_gapt/api/workspaces/${workspace_id}/clone-log`, { tail_bytes: 4096 })
          .catch(() => undefined);
      }
      return { ...ws, ...(clone_log ? { clone_log } : {}) };
    },
  );

  tool(
    server,
    "manage_workspace",
    "Workspace lifecycle: 'start' (boot a stopped sandbox), 'stop' (pause it), 'delete' (archive + tear down — destructive, confirm with the user first), 'rehydrate' (re-clone any project repos missing from the worktree — fixes git.repo_not_cloned).",
    {
      workspace_id: z.string(),
      action: z.enum(["start", "stop", "delete", "rehydrate"]),
    },
    ({ workspace_id, action }) => {
      switch (action) {
        case "start":
          return gapt.post(`/_gapt/api/workspaces/${workspace_id}/start`);
        case "stop":
          return gapt.post(`/_gapt/api/workspaces/${workspace_id}/stop`);
        case "delete":
          return gapt.delete(`/_gapt/api/workspaces/${workspace_id}`);
        case "rehydrate":
          return gapt.post(`/_gapt/api/workspaces/${workspace_id}/rehydrate`);
      }
    },
  );

  tool(
    server,
    "run_command",
    "Execute a shell command inside the workspace's sandbox container. cwd is relative to /workspace (e.g. 'geny' to run inside that repo). Returns { exit_code, output, duration_ms } with stdout+stderr merged in order. Keep commands bounded — output is captured whole, not streamed. This is the general-purpose build/test/inspect hammer.",
    {
      workspace_id: z.string(),
      command: z.string().min(1).max(2000).describe("shell command, e.g. 'npm test -- --run'"),
      cwd: z.string().max(512).optional().describe("subdir under /workspace; omit = /workspace"),
    },
    async ({ workspace_id, command, cwd }) => {
      // The backend streams SSE (`data: {...}` lines: meta / log /
      // done). Collapse that into one structured result so the
      // calling agent gets clean output instead of protocol framing.
      const raw = await gapt.post<string>(`/_gapt/api/workspaces/${workspace_id}/tests/run`, {
        command,
        ...(cwd ? { cwd } : {}),
      });
      if (typeof raw !== "string") return raw;
      const lines: string[] = [];
      let exit_code: number | null = null;
      let duration_ms: number | null = null;
      for (const line of raw.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try {
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === "log") lines.push(ev.line ?? "");
          else if (ev.type === "done") {
            exit_code = ev.exit_code ?? null;
            duration_ms = ev.duration_ms ?? null;
          }
        } catch {
          /* partial frame — skip */
        }
      }
      return { exit_code, duration_ms, output: lines.join("\n") };
    },
  );
}
