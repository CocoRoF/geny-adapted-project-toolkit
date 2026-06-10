import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { GaptClient } from "../client.js";
import { tool } from "../register.js";

export function registerServiceTools(server: McpServer, gapt: GaptClient): void {
  tool(
    server,
    "list_services",
    "Managed dev-server processes running inside the workspace container (label, cmd, state, port, bound preview URL).",
    { workspace_id: z.string() },
    ({ workspace_id }) => gapt.get(`/_gapt/api/workspaces/${workspace_id}/services`),
  );

  tool(
    server,
    "start_service",
    "Start a long-running dev server inside the workspace (e.g. cmd='npm run dev', port=3000). GAPT keeps it alive, captures logs, and can expose it at a preview URL. Label is your handle for stop/restart/expose.",
    {
      workspace_id: z.string(),
      label: z.string().min(1).max(60).describe("short handle, e.g. 'web'"),
      cmd: z.string().min(1).max(1000),
      port: z.number().int().min(1).max(65535).optional(),
      env: z.record(z.string()).optional(),
      cwd: z.string().optional().describe("subdir under /workspace"),
    },
    ({ workspace_id, ...body }) =>
      gapt.post(`/_gapt/api/workspaces/${workspace_id}/services`, body),
  );

  tool(
    server,
    "manage_service",
    "Control one service by label: 'stop', 'restart', 'remove' (stop + forget), 'expose' (publish a public preview URL via the gateway), 'unexpose'.",
    {
      workspace_id: z.string(),
      label: z.string(),
      action: z.enum(["stop", "restart", "remove", "expose", "unexpose"]),
      port: z.number().int().optional().describe("expose only — overrides the service's port"),
    },
    ({ workspace_id, label, action, port }) => {
      const base = `/_gapt/api/workspaces/${workspace_id}/services/${encodeURIComponent(label)}`;
      switch (action) {
        case "stop":
          return gapt.post(`${base}/stop`);
        case "restart":
          return gapt.post(`${base}/restart`);
        case "remove":
          return gapt.delete(base);
        case "expose":
          return gapt.post(`${base}/expose`, port ? { port } : {});
        case "unexpose":
          return gapt.delete(`${base}/expose`);
      }
    },
  );
}
