"""Phase N — error codes for the scaffold pipeline.

Mirrors the executor's ``exec.*`` namespace approach: every distinct
failure mode gets a stable string code so the front-end can render a
friendly message + decide whether to retry, redirect to Settings, etc.
"""

from __future__ import annotations

from enum import Enum


class ScaffoldErrorCode(str, Enum):
    # token lifecycle
    TOKEN_MISSING = "github.token_missing"
    TOKEN_SCOPE_INSUFFICIENT = "github.token_scope_insufficient"
    TOKEN_INVALID = "github.token_invalid"
    # GitHub API
    REPO_EXISTS = "github.repo_exists"
    USER_FETCH_FAILED = "github.user_fetch_failed"
    CREATE_FAILED = "github.create_failed"
    DELETE_FAILED = "github.delete_failed"
    # scaffold side
    PRESET_UNKNOWN = "scaffold.preset_unknown"
    OPTION_INVALID = "scaffold.option_invalid"
    PUSH_FAILED = "scaffold.push_failed"
    RENDER_FAILED = "scaffold.render_failed"


class ScaffoldError(RuntimeError):
    """Domain error carrying a stable code + human-readable reason.

    The router layer catches this and maps to the matching HTTP status
    (see ``routers/scaffolds.py::_http_from_scaffold_error``)."""

    def __init__(self, code: ScaffoldErrorCode, reason: str) -> None:
        super().__init__(f"{code.value}: {reason}")
        self.code = code
        self.reason = reason
