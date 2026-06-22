"""geny-executor 2.2.0 migration smoke — run with:

    cd server && .venv/bin/python scripts/smoke_executor_220.py

Exercises the full post-migration path with zero network / docker /
claude-binary dependencies:

1. Load the bundled `gapt_default` manifest via GaptEnvironmentService.
2. `Pipeline.from_manifest_async(strict=True)` with stub credentials.
3. Attach a CLI client built through the 2.2.0 `runner_factory` seam
   (fake factory recording every construction) — the #866 guard must
   allow it (claude_code_cli == claude_code_cli, no override flag).
4. Drive ONE turn via GAPT's real `_drive_pipeline` against a mock
   client that streams canonical chunks (text_delta / tool_use /
   tool_result / message_complete).
5. Assert the SessionEvent frames show the tool_use mapping the old
   monkey-patch existed for.
6. `await pipeline.aclose()` (the 2.2.0 teardown owed by hosts).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT / "src"))

from geny_executor import CredentialBundle, EventTypes, ModelOverrides  # noqa: E402
from geny_executor.llm_client.types import APIResponse, ContentBlock, TokenUsage  # noqa: E402

from gapt_server.agent.credentials import build_claude_code_cli_creds  # noqa: E402
from gapt_server.agent.environment_service import GaptEnvironmentService  # noqa: E402
from gapt_server.agent.hooks.cost_hook import CostAccumulator  # noqa: E402
from geny_executor.llm_client import ContainerCLIRunner  # noqa: E402

from gapt_server.agent.sandbox_runner import (  # noqa: E402
    build_sandboxed_cli_client,
)
from gapt_server.agent.session_registry import SessionRuntime, _drive_pipeline  # noqa: E402
from gapt_server.agent.streaming import SessionEventKind  # noqa: E402


class _FakeSandbox:
    workspace_id = "smoke-ws"
    worktree_path = "/tmp/smoke"
    container_name = "gapt-ws-smoke"

    async def ensure(self) -> None:  # pragma: no cover — never spawns here
        return None


class _Caps:
    is_subprocess = True


class _MockCLIClient:
    """Streams the canonical 2.2.0 chunk vocabulary — what the real
    ClaudeCodeCLIClient's translator yields for a tool-running turn."""

    provider = "claude_code_cli"
    capabilities = _Caps()

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_message_stream(self, **kwargs: Any):
        self.calls.append(kwargs)
        yield {"type": "text_delta", "text": "Let me check. "}
        yield {
            "type": "tool_use",
            "id": "toolu_smoke_1",
            "name": "Bash",
            "input": {"command": "ls"},
            "index": 0,
        }
        yield {
            "type": "tool_result",
            "tool_use_id": "toolu_smoke_1",
            "content": "README.md\n",
            "is_error": False,
        }
        yield {"type": "thinking_delta", "text": "files look fine"}
        yield {"type": "text_delta", "text": "Done."}
        yield {
            "type": "message_complete",
            "response": APIResponse(
                content=[ContentBlock(type="text", text="Let me check. Done.")],
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=10, output_tokens=20),
                model="claude-sonnet-4-6",
            ),
        }

    async def create_message(self, **kwargs: Any) -> APIResponse:
        self.calls.append(kwargs)
        return APIResponse(
            content=[ContentBlock(type="text", text="Done.")],
            stop_reason="end_turn",
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            model="claude-sonnet-4-6",
        )


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ok  {label}")

    # 1) Resolve the bundled manifest.
    env = GaptEnvironmentService()
    resolution = env.resolve("gapt_default")
    assert resolution.source == "server_bundled"
    ok("bundled manifest gapt_default resolved")

    # 2) Strict build with stub credentials.
    fake_bin = Path("/tmp/smoke-claude")
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    creds = build_claude_code_cli_creds(
        binary_path=str(fake_bin), api_key="sk-smoke", workspace_root="/tmp/smoke"
    )
    bundle = CredentialBundle(by_provider={"claude_code_cli": creds})
    from geny_executor import Pipeline

    pipeline = await Pipeline.from_manifest_async(
        resolution.manifest, credentials=bundle, strict=True
    )
    ok("Pipeline.from_manifest_async(strict=True) built")

    # 3) runner_factory seam — fake factory recording constructions.
    factory_calls: list[dict[str, Any]] = []
    sandbox = _FakeSandbox()
    client = build_sandboxed_cli_client(creds=creds, sandbox=sandbox)
    orig_factory = client._runner_factory

    def _recording_factory(**kw: Any) -> Any:
        factory_calls.append(kw)
        return orig_factory(**kw)

    client._runner_factory = _recording_factory
    runner = client._make_runner()
    assert isinstance(runner, ContainerCLIRunner)
    assert factory_calls and factory_calls[0]["binary"] == str(fake_bin)
    assert factory_calls[0]["env_extras"]["ANTHROPIC_API_KEY"] == "sk-smoke"
    ok("runner_factory invoked with binary/cwd/env_extras/timeout kwargs")

    # #866 guard: claude_code_cli == claude_code_cli → allowed, no flag.
    pipeline.attach_runtime(llm_client=client)
    ok("attach_runtime(llm_client=) allowed (provider matches manifest)")

    # 4) One turn through GAPT's real event mapping, with the mock
    #    streaming client swapped in (no real CLI spawn).
    mock = _MockCLIClient()
    pipeline.attach_runtime(llm_client=mock)
    runtime = SessionRuntime(
        session_id="smoke",
        project_id="p",
        workspace_id="w",
        user_id="u",
        pipeline=pipeline,
        accumulator=CostAccumulator(session_id="smoke"),
    )
    runtime.apply_per_invoke_overrides(
        model=None, thinking_enabled=None, thinking_budget_tokens=None, clear=None
    )
    assert runtime.pending_model_overrides() is None
    frames: list[tuple[SessionEventKind, dict]] = []

    async def _capture(kind: SessionEventKind, data: dict) -> Any:
        frames.append((kind, data))

    runtime.bus.publish = _capture  # type: ignore[assignment]
    await _drive_pipeline(runtime, "list the files")
    assert mock.calls, "mock client was never called"
    ok("run_stream one turn completed against mock client")

    tool_calls = [d for k, d in frames if k is SessionEventKind.TOOL_CALL]
    assert tool_calls and tool_calls[0]["tool"] == "Bash"
    assert tool_calls[0]["tool_use_id"] == "toolu_smoke_1"
    ok("api.tool_use mapped to TOOL_CALL frame (patch-era gap closed)")

    tool_results = [d for k, d in frames if k is SessionEventKind.TOOL_RESULT]
    assert tool_results and tool_results[0]["content"] == "README.md\n"
    ok("api.tool_result mapped to TOOL_RESULT frame")

    texts = "".join(d.get("text", "") for k, d in frames if k is SessionEventKind.TEXT)
    assert "Let me check." in texts and "Done." in texts
    ok("text.delta still streams chat text")

    steps = [d for k, d in frames if k is SessionEventKind.STEP]
    assert any(d["phase"] == "thinking" for d in steps)
    ok("thinking.delta surfaced in the step trace")

    # ModelOverrides smoke — a per-run override is accepted by run_stream.
    state_events: list[str] = []
    async for ev in pipeline.run_stream(
        "again", overrides=ModelOverrides(model="claude-opus-4-7")
    ):
        state_events.append(str(ev.type))
    assert EventTypes.CONFIG_OVERRIDE_APPLIED.value in state_events
    ok("run_stream(overrides=ModelOverrides(...)) emits config.override_applied")

    # 6) Teardown owed by 2.2.0 hosts.
    await pipeline.aclose()
    ok("pipeline.aclose() completed")

    print(f"\nSMOKE PASSED — {len(checks)} checks")


if __name__ == "__main__":
    asyncio.run(main())
