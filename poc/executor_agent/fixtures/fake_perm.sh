#!/usr/bin/env bash
# Fixture for `exec.cli.permission_denied` repro.
# Emits stderr matching the heuristic ("permission ... denied|deny|blocked").
echo "Tool execution blocked by permission rule: permission denied" >&2
exit 1
