"""LLM backend health + Claude Code auth.

Phase G.1 — port of Geny's `llm_backends_controller` adapted to
GAPT's vault + single-admin model. Exports the public types the
router builds on.
"""

from gapt_server.domains.llm_backends.auth_jobs import (
    AuthJob,
    cancel_job,
    get_job,
    list_jobs,
    reap_old_jobs,
    reset_registry,
    spawn_auth_job,
    submit_input,
)
from gapt_server.domains.llm_backends.health import (
    PROVIDER_LABELS,
    ProviderHealth,
    claude_binary_path,
    collect_health,
)

__all__ = [
    "PROVIDER_LABELS",
    "AuthJob",
    "ProviderHealth",
    "cancel_job",
    "claude_binary_path",
    "collect_health",
    "get_job",
    "list_jobs",
    "reap_old_jobs",
    "reset_registry",
    "spawn_auth_job",
    "submit_input",
]
