import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { GaptClient } from "../client.js";
import { tool } from "../register.js";

export function registerDeployTools(server: McpServer, gapt: GaptClient): void {
  tool(
    server,
    "list_environments",
    "Deploy environments of a project (id, name, target kind, which repository they deploy). Environment ids feed deploy_environment / environment_stack.",
    { project_id: z.string() },
    ({ project_id }) => gapt.get(`/_gapt/api/projects/${project_id}/environments`),
  );

  tool(
    server,
    "deploy_environment",
    "Kick off an async deploy of one environment. Returns a run_id immediately — poll get_deploy_run until status is succeeded/failed. Pass version to pin a git ref; omit for the latest.",
    {
      env_id: z.string(),
      version: z.string().optional().describe("git ref / tag; omit = latest"),
    },
    ({ env_id, version }) =>
      gapt.post(`/_gapt/api/environments/${env_id}/deploy/async`, {
        ...(version ? { version } : {}),
      }),
  );

  tool(
    server,
    "get_deploy_run",
    "Status + step detail + log excerpt of one deploy run. Poll this after deploy_environment.",
    { run_id: z.string() },
    async ({ run_id }) => {
      const [run, detail] = await Promise.all([
        gapt.get(`/_gapt/api/deploy/runs/${run_id}`),
        gapt.get(`/_gapt/api/deploy/runs/${run_id}/detail`).catch(() => undefined),
      ]);
      return { run, detail };
    },
  );

  tool(
    server,
    "environment_stack",
    "Inspect/control the running compose stack of an environment: 'status' (containers + health), 'logs' (recent stack logs; tail param), 'restart', 'down' (stop the stack — outage, confirm with the user first).",
    {
      env_id: z.string(),
      action: z.enum(["status", "logs", "restart", "down"]),
      tail: z.number().int().min(10).max(2000).default(200).describe("logs only"),
    },
    ({ env_id, action, tail }) => {
      const base = `/_gapt/api/environments/${env_id}/stack`;
      switch (action) {
        case "status":
          return gapt.get(base);
        case "logs":
          return gapt.get(`${base}/logs`, { tail });
        case "restart":
          return gapt.post(`${base}/restart`);
        case "down":
          return gapt.post(`${base}/down`);
      }
    },
  );

  tool(
    server,
    "rollback_environment",
    "Roll an environment back to a previously deployed version. Needs the failing run_id and the to_version to restore — get both from list_deploy_runs.",
    {
      env_id: z.string(),
      run_id: z.string().describe("the run being rolled back"),
      to_version: z.string().describe("version string of the known-good deploy"),
    },
    ({ env_id, run_id, to_version }) =>
      gapt.post(`/_gapt/api/environments/${env_id}/rollback`, { run_id, to_version }),
  );

  tool(
    server,
    "list_deploy_runs",
    "Deploy history of an environment (run ids, versions, status, timestamps). Source of to_version for rollback_environment.",
    { env_id: z.string() },
    ({ env_id }) => gapt.get(`/_gapt/api/environments/${env_id}/runs`),
  );

  tool(
    server,
    "cost_summary",
    "LLM spend summary across the instance (per-project rollup). Answers 'how much have we spent'.",
    {},
    () => gapt.get("/_gapt/api/cost/summary"),
  );
}
