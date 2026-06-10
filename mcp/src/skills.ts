/** The GAPT usage skill — one canonical document, surfaced three ways:
 *  1. Server `instructions` (clients inject into the system prompt)
 *  2. MCP resource  `gapt://skill/usage`
 *  3. MCP prompt    `gapt_usage_guide`
 *
 * Keep this the single source of truth for HOW an agent should drive
 * GAPT. Tool descriptions say what each tool does; this says how the
 * pieces compose. */

export const GAPT_SKILL = `# GAPT MCP — usage skill

GAPT is a self-hosted project sandbox platform. Every project holds zero-or-more
git repositories; every **workspace** is an isolated docker sandbox with those
repos cloned side-by-side. You (the agent) can browse/edit files, run commands,
drive git per-repo, manage dev-server processes, launch GAPT's own coding agent,
and deploy environments — all through these tools.

## Core model (read this first)

- **Project** — logical container. Has \`repositories[]\` (each: id, subpath,
  git_remote_url|null, default_branch). A repository with NO remote URL is an
  "empty folder candidate" (git init later).
- **Workspace** — named docker sandbox of one project. Created with
  \`selections\` = which repos to clone at which branch. Multi-repo workspaces
  mount each repo at \`/workspace/<subpath>/\`. Workspace status:
  creating → running → (stopped|failed|archived).
- **repo_id** — git_* tools take an optional \`repo_id\`. ALWAYS pass it on
  multi-repo projects; omitting falls back to the project's primary repo which
  may not be the one you mean. Get ids from \`list_repositories\`.
- **Environment** — a deploy target of a project (compose-based). Deploys are
  async runs you can poll.
- **Agent session** — GAPT's built-in coding agent bound to a workspace. Use it
  to delegate whole tasks ("fix the failing test and commit") instead of doing
  every edit yourself.

## Golden workflows

### Explore an instance
1. \`gapt_overview\` → projects + active workspaces + caps at a glance.
2. \`get_project\` → repositories + environments of one project.

### Start working on code
1. \`list_repositories(project_id)\` → note repo ids + default branches.
2. \`create_workspace(project_id, name, selections=[{repository_id, branch}])\`
   — name must be unique per project among live workspaces; re-creating the
   same name returns the existing one (idempotent).
3. Poll \`get_workspace\` until status="running" (clone is async; check
   \`clone_log\` if it stays "creating").
4. \`list_files\` / \`read_file\` / \`write_file\` to edit;
   \`run_command\` to build/test (cwd defaults to /workspace).

### Git (per repo!)
- \`git_status(workspace_id, repo_id)\` → branch, ahead/behind, dirty files.
- \`git_commit(workspace_id, repo_id, message, paths?)\` — stages+commits.
- \`git_sync(workspace_id, repo_id, mode="sync")\` — fetch+pull+push in one go;
  or mode="fetch"|"pull"|"push" individually. First push of a new branch sets
  upstream automatically.
- \`git_checkout(workspace_id, repo_id, branch, create=true)\` for new branches.
- \`create_pr(workspace_id, repo_id, title, body?, base?)\` — opens a GitHub PR.
- A 412 with code \`git.repo_not_cloned\` means this repo isn't on the
  workspace's disk → call \`manage_workspace(action="rehydrate")\` once, retry.

### Run / preview a dev server
1. \`start_service(workspace_id, label, cmd, port?)\` — e.g. cmd="npm run dev".
2. \`list_services\` shows state + bound URL; \`expose_service\` publishes a
   preview URL through the gateway.

### Delegate to GAPT's coding agent
- One-shot: \`agent_oneshot(workspace_id, message)\` — blocks until done,
  returns the final answer. Best for self-contained tasks ≤ a few minutes.
- Long-lived: \`create_agent_session\` → \`agent_invoke(session_id, message)\`
  (returns immediately) → poll \`agent_messages\` for progress/results.

### Deploy
1. \`list_environments(project_id)\` → env ids + target kind.
2. \`deploy_environment(env_id)\` → returns run_id (async).
3. Poll \`get_deploy_run(run_id)\` until status succeeded|failed.
4. \`environment_stack(env_id, action="logs")\` to inspect;
   \`rollback_environment(env_id)\` if the new version is bad.

## Rules of engagement
- Workspace file paths are RELATIVE to /workspace (e.g. "geny/src/app.py").
  Never use absolute host paths.
- \`run_command\` executes inside the sandbox container — safe, but long
  commands should set timeout_s; output is captured, not streamed.
- Destructive tools (delete_path, manage_workspace delete, git_discard,
  stack down) — confirm intent with the user unless they explicitly asked.
- Errors come back as \`[status] code: reason\`. 401/403 → credentials problem;
  409 workspace.not_running → start the workspace first;
  412 repository.none → project has no repos yet.
- Costs: agent sessions bill LLM usage. Check \`cost_summary\` when asked
  about spend.
`;
