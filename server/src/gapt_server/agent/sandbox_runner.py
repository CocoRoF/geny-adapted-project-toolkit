"""Docker-sandbox CLI spawning via geny-executor 2.2.0's supported seam.

Pre-2.2.0 this lived in ``executor_patches.py`` as a monkey-patch on
the private ``CLIProcessRunner._spawn`` (plus a ContextVar so two
concurrent sessions could target different containers). 2.2.0 ships
``ClaudeCodeCLIClient(runner_factory=...)`` — a constructor kwarg that
receives ``binary= / cwd= / env_extras= / timeout_s=`` and returns the
``CLIProcessRunner`` (or a compatible subclass) every spawn AND the
version-handshake probe route through.

GAPT's wiring (documented here because the credentials path can't
carry a callable — ``runner_factory`` is not mapped from
``ProviderCredentials.extras`` by the executor's
``_creds_to_client_kwargs``):

1. ``ProjectAwareSessionManager`` builds the ``CredentialBundle`` as
   before and exposes the ``claude_code_cli`` ``ProviderCredentials``
   on the ``AgentSessionHandle`` (``cli_credentials``).
2. The session bootstrap (``routers/sessions._build_runtime_from_handle``
   and the oneshot equivalent) — the first place that knows the
   workspace's :class:`WorkspaceSandbox` — calls
   :func:`build_sandboxed_cli_client` and attaches the client with
   ``pipeline.attach_runtime(llm_client=client, hook_runner=...)``.
   The 2.2.0 #866 guard allows this without an override flag because
   the client's ``provider`` (``claude_code_cli``) matches the
   manifest's declared Stage 6 provider.
3. With no sandbox bound (tests, host-execution paths) nothing is
   attached and the pipeline resolves its client from the credential
   bundle as usual — identical to upstream behaviour.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import TYPE_CHECKING, Any

import structlog
from geny_executor.llm_client._cli_runtime import CLIProcessRunner
from geny_executor.llm_client.claude_code import ClaudeCodeCLIClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    from geny_executor import ProviderCredentials

    from gapt_server.domains.workspace_sandbox import WorkspaceSandbox

logger = structlog.get_logger(__name__)


class SandboxedCLIProcessRunner(CLIProcessRunner):
    """``CLIProcessRunner`` that spawns inside a workspace container.

    Only ``_spawn`` differs from the parent: argv becomes
    ``docker exec -i -w /workspace --env ... <gapt-ws-X> claude <argv>``
    so the agent only ever sees the workspace's bind-mounted
    ``/workspace`` — never the GAPT operator's host filesystem.

    Everything else (timeout ladder, SIGTERM→SIGKILL process-group
    teardown, stderr collection) is inherited: ``start_new_session``
    is preserved on POSIX so killing the host-side ``docker exec``
    group propagates to the ``claude`` process inside the container.
    """

    def __init__(self, *, sandbox: WorkspaceSandbox, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sandbox = sandbox

    async def _spawn(self, argv: Sequence[str]) -> tuple[asyncio.subprocess.Process, float]:
        sandbox = self.sandbox
        # Sandbox might not have a container running yet — first agent
        # call after a server restart. ensure() is idempotent.
        try:
            await sandbox.ensure()
        except Exception:
            logger.warning(
                "sandbox_runner.ensure_failed",
                workspace_id=sandbox.workspace_id,
                container=sandbox.container_name,
            )

        docker_argv: list[str] = ["exec", "-i", "-w", "/workspace"]
        for k, v in dict(self.env_extras or {}).items():
            docker_argv += ["--env", f"{k}={v}"]
        # Inside the container the agent CLI is always `claude` on PATH
        # (the gapt-workspace image installs it via npm). We deliberately
        # don't forward `self.binary` (a host-side path that doesn't
        # exist in the container).
        docker_argv += [sandbox.container_name, "claude", *list(argv)]

        kwargs: dict[str, Any] = dict(
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # The docker binary needs the *host* env (PATH, DOCKER_HOST,
            # ...). The child's view of env is what we passed via --env
            # flags above; that's separate.
            env=os.environ.copy(),
            cwd=None,
        )
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        proc = await asyncio.create_subprocess_exec("docker", *docker_argv, **kwargs)
        return proc, time.monotonic()


def _client_kwargs_from_creds(creds: ProviderCredentials) -> dict[str, Any]:
    """Map GAPT's ``claude_code_cli`` ``ProviderCredentials`` (built by
    ``agent.credentials.build_claude_code_cli_creds``) onto
    ``ClaudeCodeCLIClient`` constructor kwargs.

    Mirrors the executor's internal ``_creds_to_client_kwargs`` for the
    extras GAPT actually sets; kept GAPT-side because the public
    construction path has no creds→client helper and we own both ends
    of this dict."""
    extras = dict(creds.extras or {})
    kwargs: dict[str, Any] = {"api_key": creds.api_key}
    if creds.binary_path:
        kwargs["binary_path"] = creds.binary_path
    auth_mode = getattr(creds, "auth_mode", "auto")
    if auth_mode != "auto":
        kwargs["auth_mode"] = auth_mode
    for key in (
        "workspace_dir",
        "workspace_root",
        "settings_path",
        "bare_mode",
        "max_budget_usd",
        "default_permission_mode",
        "mcp_config",
        "allow_tools",
        "disallow_tools",
        "extra_args",
        "timeout_s",
        "strict_wire",
    ):
        if key in extras:
            # workspace_root is the settings-side name; the client
            # constructor takes workspace_dir.
            if key == "workspace_root":
                kwargs["workspace_dir"] = extras[key]
            else:
                kwargs[key] = extras[key]
    return kwargs


def build_sandboxed_cli_client(
    *,
    creds: ProviderCredentials,
    sandbox: WorkspaceSandbox,
) -> ClaudeCodeCLIClient:
    """Construct a ``ClaudeCodeCLIClient`` whose every process spawn
    (including the one-time ``--version`` handshake) runs inside
    ``sandbox``'s container via :class:`SandboxedCLIProcessRunner`."""

    def _factory(**runner_kwargs: Any) -> CLIProcessRunner:
        return SandboxedCLIProcessRunner(sandbox=sandbox, **runner_kwargs)

    return ClaudeCodeCLIClient(
        **_client_kwargs_from_creds(creds),
        runner_factory=_factory,
    )


def attach_session_runtime(
    *,
    pipeline: Any,
    hook_runner: Any,
    sandbox: WorkspaceSandbox | None,
    cli_credentials: ProviderCredentials | None,
    session_id: str = "",
) -> None:
    """One-stop ``attach_runtime`` for GAPT's session bootstrap.

    With a sandbox + CLI credentials available, attaches a sandboxed
    ``ClaudeCodeCLIClient`` alongside the hook runner. 2.2.0's #866
    guard ALLOWS the ``llm_client=`` attach without an override flag
    when the client's provider matches the manifest's Stage 6
    declaration (``claude_code_cli`` == ``claude_code_cli``); for a
    manifest that declares an SDK provider instead (anthropic /
    openai / google / vllm) the guard raises ``ConfigError`` — we then
    fall back to the hook-only attach, which is correct: such a
    pipeline never spawns the CLI, so there is nothing to sandbox.
    """
    if sandbox is not None and cli_credentials is not None:
        try:
            client = build_sandboxed_cli_client(creds=cli_credentials, sandbox=sandbox)
            pipeline.attach_runtime(llm_client=client, hook_runner=hook_runner)
        except Exception as exc:  # ConfigError (provider mismatch) or ctor failure
            logger.info(
                "sandbox_runner.llm_client_attach_skipped",
                session_id=session_id,
                workspace_id=sandbox.workspace_id,
                reason=f"{type(exc).__name__}: {exc}"[:300],
            )
            pipeline.attach_runtime(hook_runner=hook_runner)
        return
    pipeline.attach_runtime(hook_runner=hook_runner)
