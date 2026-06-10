#!/usr/bin/env node
/** Smoke test: boot dist/index.js over stdio, list tools, and (when
 * GAPT_SMOKE_LIVE=1) call gapt_overview against the configured
 * instance. Used by `npm run smoke` and CI. */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const env = {
  ...process.env,
  GAPT_BASE_URL: process.env.GAPT_BASE_URL ?? "http://localhost:38001",
  GAPT_LOGIN_ID: process.env.GAPT_LOGIN_ID ?? "admin",
  GAPT_LOGIN_PW: process.env.GAPT_LOGIN_PW ?? "admin",
};

const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["dist/index.js"],
  env,
  stderr: "inherit",
});

const client = new Client({ name: "gapt-mcp-smoke", version: "0.0.0" });
await client.connect(transport);

const { tools } = await client.listTools();
console.log(`tools: ${tools.length}`);
for (const t of tools) console.log(`  - ${t.name}`);

const { resources } = await client.listResources();
console.log(`resources: ${resources.map((r) => r.uri).join(", ")}`);

const { prompts } = await client.listPrompts();
console.log(`prompts: ${prompts.map((p) => p.name).join(", ")}`);

if (process.env.GAPT_SMOKE_LIVE === "1") {
  console.log("\n--- live: gapt_overview ---");
  const res = await client.callTool({ name: "gapt_overview", arguments: {} });
  const text = res.content?.[0]?.text ?? "";
  console.log(text.slice(0, 600));
  if (res.isError) {
    console.error("LIVE CALL FAILED");
    process.exit(1);
  }
}

await client.close();
console.log("\nsmoke OK");
