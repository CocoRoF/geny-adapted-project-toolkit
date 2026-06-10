import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { GaptClient } from "../client.js";
import { tool } from "../register.js";

/** GAPT ships its own coding agent (geny-executor pipeline) that runs
 * INSIDE a workspace with full tool access. These tools let the
 * calling agent delegate whole tasks to it. */
export function registerAgentTools(server: McpServer, gapt: GaptClient): void {
  tool(
    server,
    "agent_oneshot",
    "Fire one self-contained task at GAPT's built-in coding agent inside a workspace and BLOCK until it finishes (or timeout_s). Returns the agent's final answer + cost. Best for bounded tasks: 'run the tests and summarize failures', 'fix the lint errors and commit'. For long multi-turn work use create_agent_session + agent_invoke instead.",
    {
      workspace_id: z.string(),
      message: z.string().min(1).max(20_000).describe("the task, written like a prompt to a coding agent"),
      timeout_s: z.number().int().min(30).max(3600).default(600),
    },
    ({ workspace_id, message, timeout_s }) =>
      gapt.post("/_gapt/api/sessions/oneshot", { workspace_id, message, timeout_s }),
  );

  tool(
    server,
    "create_agent_session",
    "Open a persistent agent session bound to a workspace (conversation + budget caps). Returns session_id for agent_invoke / agent_messages.",
    {
      project_id: z.string(),
      workspace_id: z.string(),
      model: z.string().max(120).optional().describe("override the default model"),
      cost_budget_usd: z.number().max(1000).optional().describe("hard spend cap for the session"),
      max_iterations: z.number().int().max(200).optional(),
    },
    ({ project_id, ...body }) =>
      gapt.post(`/_gapt/api/projects/${project_id}/sessions`, body),
  );

  tool(
    server,
    "agent_invoke",
    "Send a message to an agent session. Returns immediately (the agent works in the background) — poll agent_messages to watch progress and get results.",
    {
      session_id: z.string(),
      message: z.string().min(1).max(50_000),
    },
    ({ session_id, message }) =>
      gapt.post(`/_gapt/api/sessions/${session_id}/invoke`, { message }),
  );

  tool(
    server,
    "agent_messages",
    "Read an agent session's message log (newest last): user/assistant turns, tool calls, status. Use to poll a running invoke and to collect the final answer.",
    {
      session_id: z.string(),
      limit: z.number().int().min(1).max(200).default(50),
    },
    ({ session_id, limit }) =>
      gapt.get(`/_gapt/api/sessions/${session_id}/messages`, { limit }),
  );

  tool(
    server,
    "agent_interrupt",
    "Stop a running agent session turn (the session stays alive for further invokes).",
    { session_id: z.string() },
    ({ session_id }) => gapt.post(`/_gapt/api/sessions/${session_id}/interrupt`),
  );
}
