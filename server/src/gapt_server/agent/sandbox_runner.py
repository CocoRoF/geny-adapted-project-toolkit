"""Docker-sandbox CLI spawning via geny-executor's built-in container runner.

History:
- Pre-2.2.0 this monkey-patched the private ``CLIProcessRunner._spawn``
  (pinning GAPT to 2.1.0).
- 2.2.0 added ``ClaudeCodeCLIClient(runner_factory=...)`` and GAPT shipped its
  own ``SandboxedCLIProcessRunner`` subclass through that seam.
- **2.21.0 absorbs the runner itself into the executor** as the first-class
  ``ContainerCLIRunner`` + ``build_container_cli_client`` (Geny⇄GAPT plan, L1).
  GAPT no longer carries a runner subclass — ``WorkspaceSandbox`` already
  satisfies the executor's ``SandboxHandle`` Protocol (``container_name`` +
  idempotent async ``ensure()``), so we build the client and attach it.

GAPT's wiring (documented here because the credentials path can't carry a
callable — ``runner_factory`` is not mapped from ``ProviderCredentials.extras``
by the executor's ``_creds_to_client_kwargs``):

1. ``ProjectAwareSessionManager`` builds the ``CredentialBundle`` and exposes the
   ``claude_code_cli`` ``ProviderCredentials`` on the ``AgentSessionHandle``
   (``cli_credentials``).
2. The session bootstrap (``routers/sessions._build_runtime_from_handle`` and the
   oneshot equivalent) — the first place that knows the workspace's
   :class:`WorkspaceSandbox` — calls :func:`build_sandboxed_cli_client` and
   attaches the client with ``pipeline.attach_runtime(llm_client=client,
   hook_runner=...)``. The #866 guard allows this without an override flag because
   the client's ``provider`` (``claude_code_cli``) matches the manifest's declared
   Stage 6 provider.
3. With no sandbox bound (tests, host-execution paths) nothing is attached and the
   pipeline resolves its client from the credential bundle as usual — identical to
   upstream behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from geny_executor.llm_client import build_container_cli_client

if TYPE_CHECKING:
    from geny_executor import ProviderCredentials
    from geny_executor.llm_client import ClaudeCodeCLIClient

    from gapt_server.domains.workspace_sandbox import WorkspaceSandbox

logger = structlog.get_logger(__name__)


def _client_kwargs_from_creds(creds: ProviderCredentials) -> dict[str, Any]:
    """Map GAPT's ``claude_code_cli`` ``ProviderCredentials`` (built by
    ``agent.credentials.build_claude_code_cli_creds``) onto
    ``ClaudeCodeCLIClient`` constructor kwargs.

    Mirrors the executor's internal ``_creds_to_client_kwargs`` for the extras
    GAPT actually sets; kept GAPT-side because the public construction path has no
    creds→client helper and we own both ends of this dict."""
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
            # workspace_root is the settings-side name; the client constructor
            # takes workspace_dir.
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
    """Construct a ``ClaudeCodeCLIClient`` whose every process spawn (including
    the one-time ``--version`` handshake) runs inside ``sandbox``'s container.

    Thin wrapper over the executor's :func:`build_container_cli_client`; kept so
    GAPT call sites and the GAPT-specific creds mapping stay in one place."""
    return build_container_cli_client(
        sandbox=sandbox,
        **_client_kwargs_from_creds(creds),
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
    ``ClaudeCodeCLIClient`` alongside the hook runner. The #866 guard ALLOWS the
    ``llm_client=`` attach without an override flag when the client's provider
    matches the manifest's Stage 6 declaration (``claude_code_cli`` ==
    ``claude_code_cli``); for a manifest that declares an SDK provider instead
    (anthropic / openai / google / vllm) the guard raises ``ConfigError`` — we
    then fall back to the hook-only attach, which is correct: such a pipeline
    never spawns the CLI, so there is nothing to sandbox.
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
