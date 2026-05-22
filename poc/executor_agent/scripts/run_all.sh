#!/usr/bin/env bash
# M0-P3 PoC integration runner — fires all four PoC scripts in order
# and prints a one-line summary so the milestone can be re-verified in
# one command.
#
# Run from anywhere in the repo:
#     bash poc/executor_agent/scripts/run_all.sh
#
# Requires:
#   - uv installed
#   - `claude` CLI on PATH (or CLAUDE_BIN set)
#   - either ANTHROPIC_API_KEY env or claude OAuth subscription
#     (~/.claude/.credentials.json)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POC_DIR="$(cd "${HERE}/.." && pwd)"
cd "${POC_DIR}"

step() {
    printf '\n\n=== %s ===\n' "$1"
}

step "PR2 — Pipeline.from_manifest_async smoke (run.py)"
uv run --project . python run.py "Reply with just the number 4 — what is 2+2? Reply in exactly one short sentence."

step "PR3 — MCP stdio bridge (run_mcp.py)"
uv run --project . python run_mcp.py

step "PR4 — HookRunner attached + 2-layer probe (run_hooks.py)"
uv run --project . python run_hooks.py

step "PR5 — exec.cli.* 4-error reproduction (reproduce_errors.py)"
uv run --project . python reproduce_errors.py

step "Audit files produced"
ls -la \
    "${POC_DIR}/audit.jsonl" \
    "${POC_DIR}/audit_mcp.jsonl" \
    "${POC_DIR}/audit_hooks.jsonl" \
    "${POC_DIR}/audit_errors.jsonl" \
    "${POC_DIR}/../mcp_bridge/bridge_audit.jsonl" 2>/dev/null \
    || true

step "DONE"
printf 'M0-P3 PoC smoke complete. See:\n'
printf '  - docs/progress/m0/p3_agent_mcp_bridge.md\n'
printf '  - poc/executor_agent/decision_two_layer_policy.md\n'
printf '  - poc/executor_agent/error_codes_reproduced.md\n'
