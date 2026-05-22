// English catalog. Keys are the *source of truth*; other locales fall back here.
// Error code keys (exec.*) are reserved for geny-executor's stable identifiers
// surfaced verbatim per docs/04 §4.10 and docs/error_codes.md.

export const en = {
  // --- app shell ---
  "app.title": "GAPT — geny-adapted-project-toolkit",
  "app.phase0": "Phase 0 — docs-first. The web shell renders, but the real IDE arrives in M1-E3.",
  "app.repo_link": "Open the repository",
  "app.footer": "Apache-2.0 · CocoRoF",

  // --- locale picker ---
  "locale.label": "Language",
  "locale.en": "English",
  "locale.ko": "한국어",

  // --- exec.*.* error codes (geny-executor stable identifiers) ---
  // Populated incrementally in M1-E2 onwards. Keep keys spelt verbatim.
  "exec.api.auth.invalid_key": "API key is invalid or rejected by the provider.",
  "exec.api.rate_limited": "Rate limited — retrying automatically.",
  "exec.api.timeout": "Provider timed out — retrying.",
  "exec.api.token_limit": "Context window exceeded.",
  "exec.cli.binary_not_found": "claude CLI was not found on the runtime image.",
  "exec.cli.auth_failed": "claude CLI is not authenticated. Re-run claude auth login.",
  "exec.cli.timeout": "claude CLI subprocess timed out.",
  "exec.cli.permission_denied": "claude CLI permission system blocked the call.",
  "exec.stage.guard_rejected": "Budget or policy limit reached.",
  "exec.tool.access_denied": "PolicyEngine denied this tool call.",
  "exec.mutation.locked": "Pipeline stage is busy — retrying on next boundary.",
  "exec.mcp.connect_failed": "MCP server is unreachable.",
} as const;
