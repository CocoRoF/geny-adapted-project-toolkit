#!/usr/bin/env node
/** Deeper live exercise against a real instance — git per-repo, file
 * read, run_command. Expects ws-1741 fixtures from the dev instance;
 * tolerate absence by picking the first running workspace. */
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
const client = new Client({ name: "live-deep", version: "0.0.0" });
await client.connect(transport);

const call = async (name, args) => {
  const r = await client.callTool({ name, arguments: args });
  const text = r.content?.[0]?.text ?? "";
  console.log(`\n=== ${name} ${r.isError ? "(ERROR)" : ""} ===`);
  console.log(text.slice(0, 500));
  return { ok: !r.isError, data: safeParse(text) };
};
const safeParse = (t) => {
  try { return JSON.parse(t); } catch { return t; }
};

// 1. find a running workspace
const ov = await call("gapt_overview", {});
const ws = ov.data.active_workspaces?.find((w) => w.status === "running");
if (!ws) { console.error("no running workspace"); process.exit(1); }
console.log(`\nusing workspace ${ws.name} (${ws.id})`);

// 2. repositories of its project
const repos = await call("list_repositories", { project_id: ws.project_id });
const withRemote = repos.data.find((r) => r.git_remote_url);

// 3. git_status per repo
if (withRemote) {
  await call("git_status", { workspace_id: ws.id, repo_id: withRemote.id });
  await call("git_log", { workspace_id: ws.id, repo_id: withRemote.id, limit: 3 });
}

// 4. files
await call("list_files", { workspace_id: ws.id, path: "" });

// 5. run_command
await call("run_command", { workspace_id: ws.id, command: "echo gapt-mcp-live-ok && uname -a" });

await client.close();
console.log("\nlive-deep DONE");
