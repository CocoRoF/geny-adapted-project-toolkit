"""Reproduce all four `exec.cli.*` error codes (M0-P3 PR5).

For each scenario, we boot a fresh `Pipeline` whose `claude_code_cli`
credentials are pointed at either:
- a real binary with an obviously-broken arg/env (binary_not_found,
  timeout), or
- a tiny shell fixture in `fixtures/` that mimics the failure mode
  (auth_failed, permission_denied).

Then we `pipeline.run()` a one-liner, catch whatever fires, and record
the resulting `exec.cli.*` code so DoD §8's "audit에 코드 그대로 기록"
constraint is met.

Results are written to:
- `audit_errors.jsonl` — JSONL trace, one record per scenario
- `error_codes_reproduced.md` — human-readable summary table
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from geny_executor import (
    CredentialBundle,
    EnvironmentManifest,
    ExecutorErrorCode,
    Pipeline,
    ProviderCredentials,
)
from geny_executor.llm_client._cli_runtime import CLIResult
from geny_executor.llm_client.claude_code import _classify_cli_result

POC_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = POC_DIR / "manifests" / "gapt_default.v0.json"
FIXTURES = POC_DIR / "fixtures"
AUDIT_PATH = POC_DIR / "audit_errors.jsonl"
SUMMARY_PATH = POC_DIR / "error_codes_reproduced.md"


def _creds(*, binary_path: str, timeout_s: float = 30.0) -> CredentialBundle:
    return CredentialBundle(
        by_provider={
            "claude_code_cli": ProviderCredentials(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                binary_path=binary_path,
                extras={
                    "bare_mode": True,
                    "default_permission_mode": "default",
                    "timeout_s": timeout_s,
                    "max_budget_usd": 0.05,
                },
            )
        }
    )


async def _run_scenario(name: str, *, binary_path: str, timeout_s: float) -> dict[str, Any]:
    manifest = EnvironmentManifest.from_dict(
        json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    )

    started = time.perf_counter()
    record: dict[str, Any] = {
        "ts": time.time(),
        "scenario": name,
        "binary_path": binary_path,
        "timeout_s": timeout_s,
    }

    stage_errors: list[dict[str, Any]] = []
    try:
        pipeline = await Pipeline.from_manifest_async(
            manifest, credentials=_creds(binary_path=binary_path, timeout_s=timeout_s)
        )
        pipeline.on("stage.error", lambda evt: stage_errors.append(dict(evt.data)))
        result = await pipeline.run("ping")
        record.update(
            {
                "success": result.success,
                "error_msg": (result.error or "")[:200],
                "elapsed_s": round(time.perf_counter() - started, 2),
            }
        )
        # The stable code comes from stage.error data (pipeline wraps to
        # exec.stage.failed one level up — we want the inner CLI code).
        cli_errors = [e for e in stage_errors if (e.get("code") or "").startswith("exec.cli.")]
        if cli_errors:
            record["code"] = cli_errors[0]["code"]
            record["exception_type"] = cli_errors[0].get("exception_type")
            record["outcome"] = "stage.error captured"
        elif stage_errors:
            record["code"] = stage_errors[0].get("code")
            record["exception_type"] = stage_errors[0].get("exception_type")
            record["outcome"] = "stage.error captured (non-cli)"
        else:
            record["outcome"] = "no stage.error fired"
    except Exception as e:  # noqa: BLE001
        record.update(
            {
                "outcome": type(e).__name__,
                "message_head": str(e)[:200],
                "elapsed_s": round(time.perf_counter() - started, 2),
                "trace_head": "".join(traceback.format_exception_only(type(e), e))[:300],
            }
        )
    return record


SCENARIOS = [
    {
        "name": "exec.cli.binary_not_found",
        "kind": "pipeline",
        "binary_path": "/nonexistent/path/to/claude",
        "timeout_s": 30.0,
        "expect": ExecutorErrorCode.EXEC_CLI_BINARY_NOT_FOUND.value,
    },
    {
        "name": "exec.cli.auth_failed",
        "kind": "pipeline",
        "binary_path": str(FIXTURES / "fake_auth.sh"),
        "timeout_s": 30.0,
        "expect": ExecutorErrorCode.EXEC_CLI_AUTH_FAILED.value,
    },
    {
        "name": "exec.cli.timeout",
        "kind": "pipeline",
        "binary_path": str(FIXTURES / "fake_slow.sh"),
        "timeout_s": 2.0,
        "expect": ExecutorErrorCode.EXEC_CLI_TIMEOUT.value,
    },
    {
        "name": "exec.cli.permission_denied",
        # CLI_PERMISSION_DENIED is only reachable through the *oneshot*
        # path's `_classify_cli_result` heuristic (claude_code.py:65–69).
        # The s06_api stage's CLI flow goes through `runner.stream(...)`,
        # which raises `CLIProtocolError` on non-zero exit *without*
        # running the auth/permission heuristic. So we exercise the
        # classification function directly here — this is a fair
        # reproduction of the code, but it's worth filing a feedback
        # note upstream so the stream path also classifies on stderr.
        "kind": "classifier_unit",
        "binary_path": str(FIXTURES / "fake_perm.sh"),
        "timeout_s": 30.0,
        "expect": ExecutorErrorCode.EXEC_CLI_PERMISSION_DENIED.value,
    },
]


def _classifier_unit_scenario(name: str, expect: str) -> dict[str, Any]:
    """Exercise `_classify_cli_result` directly for codes that the
    streaming path of the CLI client doesn't surface today."""
    fake_result = CLIResult(
        returncode=1,
        stdout=b"",
        stderr=b"Tool execution blocked by permission rule: permission denied",
        duration_ms=12,
    )
    api_err = _classify_cli_result(fake_result)
    code = ExecutorErrorCode.from_category(api_err.category) if api_err.category else None
    return {
        "ts": time.time(),
        "scenario": name,
        "kind": "classifier_unit",
        "outcome": "classifier_unit captured",
        "code": code.value if code else None,
        "exception_type": "geny_executor.core.errors.APIError",
        "error_msg": str(api_err)[:200],
        "elapsed_s": 0.0,
        "expected": expect,
        "pass": (code is not None) and (code.value == expect),
    }


async def main() -> int:
    AUDIT_PATH.write_text("", encoding="utf-8")
    rows: list[dict[str, Any]] = []

    for scen in SCENARIOS:
        print(f"=== {scen['name']} ===")
        if scen.get("kind") == "classifier_unit":
            rec = _classifier_unit_scenario(scen["name"], scen["expect"])
        else:
            rec = await _run_scenario(
                scen["name"],
                binary_path=scen["binary_path"],
                timeout_s=scen["timeout_s"],
            )
            rec["expected"] = scen["expect"]
            rec["pass"] = rec.get("code") == scen["expect"]
        rows.append(rec)
        print(json.dumps(rec, indent=2))
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    # Markdown summary
    lines = [
        "# exec.cli.* 4-error reproduction (M0-P3 PR5)",
        "",
        "> Generated by `reproduce_errors.py`. Re-run with:",
        ">",
        "> ```bash",
        "> cd poc/executor_agent && uv run --project . python reproduce_errors.py",
        "> ```",
        "",
        "| Scenario | Expected code | Observed code | Elapsed (s) | Pass |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        passed = "✅" if r["pass"] else "❌"
        lines.append(
            f"| `{r['scenario']}` | `{r['expected']}` | `{r.get('code') or r.get('outcome')}` "
            f"| {r.get('elapsed_s', '?')} | {passed} |"
        )
    lines.append("")
    lines.append("## Per-scenario detail")
    lines.append("")
    for r in rows:
        lines.append(f"### `{r['scenario']}`")
        lines.append("")
        if r.get("binary_path"):
            lines.append(f"- binary: `{r['binary_path']}`")
        if r.get("timeout_s") is not None:
            lines.append(f"- timeout_s: {r['timeout_s']}")
        if r.get("kind"):
            lines.append(f"- repro kind: `{r['kind']}`")
        lines.append(f"- outcome: `{r.get('outcome')}`")
        if r.get("category"):
            lines.append(f"- ErrorCategory: `{r['category']}`")
        if r.get("code"):
            try:
                code_name = ExecutorErrorCode(r["code"]).name
            except ValueError:
                code_name = "?"
            lines.append(f"- ExecutorErrorCode: `{code_name}` = `{r['code']}`")
        if r.get("message_head"):
            head = r["message_head"].replace("\n", " ")
            lines.append(f"- message head: `{head}`")
        lines.append("")

    all_pass = all(r["pass"] for r in rows)
    lines.append(f"## Overall: {'✅ all 4 codes match' if all_pass else '❌ MISMATCH'}")

    lines.append("")
    lines.append("## Finding — feedback worth feeding upstream")
    lines.append("")
    lines.append(
        "Three of the four codes (`binary_not_found`, `auth_failed`, `timeout`) "
        "surface cleanly through `Pipeline.run` → `stage.error` event when the "
        "s06_api stage uses the **streaming** CLI path."
    )
    lines.append("")
    lines.append(
        "`exec.cli.permission_denied`, however, only surfaces via the "
        "`_classify_cli_result` heuristic in the **oneshot** path "
        "(`llm_client/claude_code.py:65–69`). The streaming path "
        "(`_cli_runtime.py:270–274`) raises a plain `CLIProtocolError` on "
        "non-zero exit without running the stderr-text heuristic, so a CLI "
        "that exits with a stderr like `permission denied` lands as "
        "`exec.cli.protocol_error` instead. PR5 reproduces the code by "
        "calling `_classify_cli_result` directly; the real fix is to teach "
        "the streaming path the same heuristic. Filing this as M1-E2 "
        "follow-up against geny-executor."
    )
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")

    print()
    print(f"audit JSONL  : {AUDIT_PATH}")
    print(f"summary      : {SUMMARY_PATH}")
    print(f"overall      : {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
