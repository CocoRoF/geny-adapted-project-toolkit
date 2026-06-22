"""`agent/sandbox_runner.py` — wiring over the executor's container runner.

Since geny-executor 2.21.0 the sandbox runner is first-class
(`ContainerCLIRunner` + `build_container_cli_client`); GAPT no longer carries a
subclass. These tests assert that a `ClaudeCodeCLIClient` built through
`build_sandboxed_cli_client` routes its runner construction through the factory
(so every spawn — including the version handshake — hits `ContainerCLIRunner`),
and that `attach_session_runtime` only attaches an `llm_client` when a sandbox is
actually bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from geny_executor.llm_client import ContainerCLIRunner
from geny_executor.llm_client._cli_runtime import CLIProcessRunner

from gapt_server.agent.credentials import build_claude_code_cli_creds
from gapt_server.agent.sandbox_runner import (
    attach_session_runtime,
    build_sandboxed_cli_client,
)

if TYPE_CHECKING:
    from pathlib import Path

    from geny_executor import ProviderCredentials


@pytest.fixture
def fake_binary(tmp_path: Path) -> str:
    """A throwaway host binary so creds construction doesn't depend on a host
    `claude` install. (`ContainerCLIRunner` ignores it — the agent binary lives
    in the container — but the GAPT creds builder records it.)"""
    p = tmp_path / "claude"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return str(p)


@dataclass
class _FakeSandbox:
    """WorkspaceSandbox stand-in — only what the runner touches (the executor's
    SandboxHandle Protocol: `container_name` + async `ensure()`)."""

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
    assert isinstance(runner, ContainerCLIRunner)
    assert runner.sandbox is sandbox
    # Creds flowed through: the client's env extras carry the API key and the
    # runner inherits the configured timeout.
    assert runner.env_extras.get("ANTHROPIC_API_KEY") == "sk-test"
    assert runner.timeout_s == 42.0


def test_container_runner_is_a_cli_process_runner() -> None:
    """The factory contract: a CLIProcessRunner subclass that only swaps
    `_spawn` (inheriting the timeout/kill/stream machinery)."""
    runner = ContainerCLIRunner(sandbox=_FakeSandbox(), binary="", timeout_s=10.0)
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
    assert isinstance(client._make_runner(), ContainerCLIRunner)


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
    """The #866 guard refuses an llm_client whose provider differs from the
    manifest's Stage 6 declaration. For an SDK-provider manifest that's the
    correct outcome — no CLI ever spawns, so nothing needs sandboxing — and the
    helper must degrade to the hook-only attach instead of failing session boot."""

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
async def test_container_spawn_builds_docker_exec_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_spawn` (now the executor's `ContainerCLIRunner`) must wrap the CLI argv
    in `docker exec -i -w /workspace --env ... <container> claude ...` and pass
    the HOST env to the docker binary itself."""
    sandbox = _FakeSandbox()
    runner = ContainerCLIRunner(
        sandbox=sandbox,
        binary="",
        env_extras={"ANTHROPIC_API_KEY": "sk-test"},
        timeout_s=5.0,
    )

    recorded: dict[str, Any] = {}

    async def _fake_exec(program: str, *argv: str, **kwargs: Any) -> Any:
        recorded["program"] = program
        recorded["argv"] = list(argv)
        recorded["kwargs"] = kwargs
        return object()

    # The spawn lives in the executor module now.
    monkeypatch.setattr(
        "geny_executor.llm_client._cli_runtime.asyncio.create_subprocess_exec",
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
