#!/usr/bin/env node
/** gapt-mcp — MCP stdio server wrapping a GAPT instance.
 *
 * Env: GAPT_BASE_URL, GAPT_LOGIN_ID, GAPT_LOGIN_PW (+ GAPT_TIMEOUT_MS).
 * Run: `npx gapt-mcp` from any MCP host (Claude Code, Claude Desktop,
 * Cursor, …). All logging goes to stderr — stdout is the protocol.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { GaptClient } from "./client.js";
import { GAPT_SKILL } from "./skills.js";
import { registerProjectTools } from "./tools/projects.js";
import { registerWorkspaceTools } from "./tools/workspaces.js";
import { registerFileTools } from "./tools/files.js";
import { registerGitTools } from "./tools/git.js";
import { registerServiceTools } from "./tools/services.js";
import { registerAgentTools } from "./tools/agent.js";
import { registerDeployTools } from "./tools/deploy.js";

const server = new McpServer(
  { name: "gapt-mcp", version: "0.1.0" },
  {
    // Injected into the client's context at connect time — the
    // always-on portion of the skill. The full document is also a
    // resource + prompt below for clients that surface those better.
    instructions: GAPT_SKILL,
  },
);

const gapt = new GaptClient();

registerProjectTools(server, gapt);
registerWorkspaceTools(server, gapt);
registerFileTools(server, gapt);
registerGitTools(server, gapt);
registerServiceTools(server, gapt);
registerAgentTools(server, gapt);
registerDeployTools(server, gapt);

// ── skill surfaces ──────────────────────────────────────────────────
server.resource(
  "gapt-usage-skill",
  "gapt://skill/usage",
  {
    description: "How to drive GAPT well: core model, golden workflows, rules of engagement",
    mimeType: "text/markdown",
  },
  async () => ({
    contents: [{ uri: "gapt://skill/usage", mimeType: "text/markdown", text: GAPT_SKILL }],
  }),
);

server.prompt(
  "gapt_usage_guide",
  "Load the GAPT usage skill into context — call when starting non-trivial GAPT work",
  () => ({
    messages: [
      {
        role: "user" as const,
        content: { type: "text" as const, text: GAPT_SKILL },
      },
    ],
  }),
);

const transport = new StdioServerTransport();
await server.connect(transport);
console.error(
  `[gapt-mcp] connected — ${process.env.GAPT_BASE_URL} as ${process.env.GAPT_LOGIN_ID}`,
);
