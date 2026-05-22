#!/usr/bin/env bash
# Fixture for `exec.cli.auth_failed`. Emits a stream-json line whose
# `error` field is `authentication_failed` — exactly the trigger the CLI
# client's streaming parser keys on at
# `llm_client/claude_code.py:302`.
printf '{"error":"authentication_failed","message":"Not logged in"}\n'
exit 1
