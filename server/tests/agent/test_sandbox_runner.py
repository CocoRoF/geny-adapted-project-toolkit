"""`agent/sandbox_runner.py` — the 2.2.0 `runner_factory` seam.

Replaces the deleted `executor_patches.py` `_spawn` monkey-patch
tests: asserts that a `ClaudeCodeCLIClient` built through
`build_sandboxed_cli_client` routes its runner construction through
the factory (so every spawn — including the version handshake — hits
`SandboxedCLIProcessRunner`), and that `attach_session_runtime` only
attaches an `llm_client` when a sandbox is actually bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from geny_executor.llm_client._cli_runtime import CLIProcessRunner

from gapt_server.agent.credentials import build_claude_code_cli_creds
from gapt_server.agent.sandbox_runner import (
    SandboxedCLIProcessRunner,
    attach_session_runtime,
    build_sandboxed_cli_client,
)

if TYPE_CHECKING:
    from pathlib import Path

    from geny_executor import ProviderCredentials


@pytest.fixture
def fake_binary(tmp_path: Path) -> str:
    """`CLIProcessRunner.__post_init__` validates the binary exists +
    is executable — provide a throwaway one so the tests don't depend
    on a host `claude` install."""
    p = tmp_path / "claude"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return str(p)


@dataclass
class _FakeSandbox:
    """WorkspaceSandbox stand-in — only what the runner touches."""

    workspace_id: str = "w1"
    worktree_path: str = "/tmp/wt"
    container_name: str = "gapt-ws-w1"
    ensured: int = 0

    async def ensure(self) -> None:
        self.ensured += 1


def _creds(binary: str, **kwargs: Any) -> ProviderCredentials:
    return build_claude_code_cli_creds(
        binary_path=binary,
        api_key="sk-test",
        workspace_root="/tmp/wt",
        timeout_s=42.0,
        **kwargs,
    )


def test_build_sandboxed_cli_client_routes_runner_through_factory(
    fake_binary: str,
) -> None:
    sandbox = _FakeSandbox()
    client = build_sandboxed_cli_client(creds=_creds(fake_binary), sandbox=sandbox)

    assert client.provider == "claude_code_cli"
    runner = client._make_runner()
    assert isinstance(runner, SandboxedCLIProcessRunner)
    assert runner.sandbox is sandbox
    # Creds flowed through: the client's env extras carry the API key
    # and the runner inherits the configured timeout.
    assert runner.env_extras.get("ANTHROPIC_API_KEY") == "sk-test"
    assert runner.timeout_s == 42.0
    assert runner.binary == fake_binary


def test_sandboxed_runner_is_a_cli_process_runner(fake_binary: str) -> None:
    """The factory contract: return a CLIProcessRunner or compatible —
    the subclass inherits the timeout/kill/stream machinery and only
    swaps `_spawn`."""
    runner = SandboxedCLIProcessRunner(sandbox=_FakeSandbox(), binary=fake_binary, timeout_s=10.0)
    assert isinstance(runner, CLIProcessRunner)
    assert type(runner)._spawn is not CLIProcessRunner._spawn


class _RecordingPipeline:
    def __init__(self) -> None:
        self.attach_calls: list[dict[str, Any]] = []

    def attach_runtime(self, **kwargs: Any) -> None:
        self.attach_calls.append(kwargs)


def test_attach_session_runtime_with_sandbox_attaches_llm_client(fake_binary: str) -> None:
    pipeline = _RecordingPipeline()
    sandbox = _FakeSandbox()
    attach_session_runtime(
        pipeline=pipeline,
        hook_runner="HOOKS",
        sandbox=sandbox,  # type: ignore[arg-type]
        cli_credentials=_creds(fake_binary),
        session_id="s1",
    )
    assert len(pipeline.attach_calls) == 1
    call = pipeline.attach_calls[0]
    assert call["hook_runner"] == "HOOKS"
    client = call["llm_client"]
    assert client.provider == "claude_code_cli"
    assert isinstance(client._make_runner(), SandboxedCLIProcessRunner)


def test_attach_session_runtime_without_sandbox_attaches_hooks_only(fake_binary: str) -> None:
    pipeline = _RecordingPipeline()
    attach_session_runtime(
        pipeline=pipeline,
        hook_runner="HOOKS",
        sandbox=None,
        cli_credentials=_creds(fake_binary),
        session_id="s1",
    )
    assert pipeline.attach_calls == [{"hook_runner": "HOOKS"}]


def test_attach_session_runtime_provider_mismatch_falls_back_to_hooks_only(
    fake_binary: str,
) -> None:
    """2.2.0's #866 guard refuses an llm_client whose provider differs
    from the manifest's Stage 6 declaration. For an SDK-provider
    manifest that's the correct outcome — no CLI ever spawns, so
    nothing needs sandboxing — and the helper must degrade to the
    hook-only attach instead of failing the session boot."""

    class _GuardingPipeline(_RecordingPipeline):
        def attach_runtime(self, **kwargs: Any) -> None:
            if "llm_client" in kwargs:
                raise ValueError("attach_runtime(llm_client=...): provider mismatch (#866)")
            super().attach_runtime(**kwargs)

    pipeline = _GuardingPipeline()
    attach_session_runtime(
        pipeline=pipeline,
        hook_runner="HOOKS",
        sandbox=_FakeSandbox(),  # type: ignore[arg-type]
        cli_credentials=_creds(fake_binary),
        session_id="s1",
    )
    assert pipeline.attach_calls == [{"hook_runner": "HOOKS"}]


@pytest.mark.asyncio
async def test_sandboxed_spawn_builds_docker_exec_argv(
    monkeypatch: pytest.MonkeyPatch, fake_binary: str
) -> None:
    """`_spawn` must wrap the CLI argv in `docker exec -i -w /workspace
    --env ... <container> claude ...` and pass the HOST env to the
    docker binary itself."""
    sandbox = _FakeSandbox()
    runner = SandboxedCLIProcessRunner(
        sandbox=sandbox,
        binary=fake_binary,
        env_extras={"ANTHROPIC_API_KEY": "sk-test"},
        timeout_s=5.0,
    )

    recorded: dict[str, Any] = {}

    async def _fake_exec(program: str, *argv: str, **kwargs: Any) -> Any:
        recorded["program"] = program
        recorded["argv"] = list(argv)
        recorded["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(
        "gapt_server.agent.sandbox_runner.asyncio.create_subprocess_exec",
        _fake_exec,
    )

    proc, t0 = await runner._spawn(["-p", "hello", "--output-format", "stream-json"])
    assert proc is not None and t0 > 0
    assert sandbox.ensured == 1
    assert recorded["program"] == "docker"
    argv = recorded["argv"]
    assert argv[:4] == ["exec", "-i", "-w", "/workspace"]
    assert argv[4:6] == ["--env", "ANTHROPIC_API_KEY=sk-test"]
    # Container + in-container binary, then the original CLI argv.
    assert argv[6:8] == ["gapt-ws-w1", "claude"]
    assert argv[8:] == ["-p", "hello", "--output-format", "stream-json"]
    # The host-side `claude` path must NOT leak into the container argv.
    assert fake_binary not in argv
