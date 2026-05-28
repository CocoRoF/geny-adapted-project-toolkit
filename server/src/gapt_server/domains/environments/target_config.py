"""Per-kind validation for `Environment.deploy_target_config`.

Phase H, H.1 — replaces the old "anything goes" `dict[str, Any]`
shape with pydantic models that enforce *known* fields strictly
while leaving room for `extra="allow"` so legacy rows with unknown
keys aren't rejected on every edit.

Why discriminated by an *external* enum (`DeployTargetKind`) instead
of a `Literal["local"]` inside each config: the `kind` lives in the
Environment row's own column, not inside `deploy_target_config`.
We don't want callers to have to repeat it inside the JSON blob.

The router calls `validate_target_config(kind, raw_dict)`; we route
to the right model, run validation, return a clean dict ready to
go straight into `Environment.deploy_target_config`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
)

from gapt_server.db import enums


# ────────────────────────────────────────────── errors ──


class TargetConfigInvalidError(ValueError):
    """Raised when `validate_target_config` finds a schema violation.

    `.fields` is a list of pydantic error dicts (loc / msg / type)
    so the API layer can surface field-level errors to the modal.
    """

    def __init__(self, message: str, *, fields: list[dict[str, Any]]):
        super().__init__(message)
        self.message = message
        self.fields = fields


class KindNotSupportedError(ValueError):
    """Raised for `DeployTargetKind` values we haven't built a schema
    for yet (currently: K8S). The router translates this into a 422
    with `code="environment.target_kind_not_supported"`."""

    def __init__(self, kind: enums.DeployTargetKind):
        super().__init__(f"deploy target kind {kind.value!r} is not supported")
        self.kind = kind


# ───────────────────────────────────────── shared atoms ──


class _BaseTargetConfig(BaseModel):
    """Common pydantic config for every kind's model.

    `extra="allow"` lets a legacy `deploy_target_config` with unknown
    keys round-trip through edit modals without being rejected — the
    UI surfaces them in a read-only "extensions" panel (Phase H.2)
    so the operator can intentionally clean them up rather than
    losing them on first save.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


# ───────────────────────────────────────────── local ──


class LocalTargetConfig(_BaseTargetConfig):
    """`kind="local"` — Docker compose stack on the GAPT host.

    Every field is optional because the deploy orchestrator has
    sane defaults (compose_path defaults to `docker-compose.yml`,
    strip_prefix=True, preview_mode="path"). What we enforce is
    *types* — the modal can't save `primary_port="3000"` (string).
    """

    # Compose file selection — either one path or many (the latter
    # lets the operator chain `-f a.yml -f b.yml` for layered configs).
    compose_path: str | None = None
    compose_paths: list[str] = Field(default_factory=list)

    # Preview routing
    preview_mode: Literal["path", "subdomain"] | None = None
    # DNS-safe slug. Allow the EnvSettingsModal's regex to be the
    # canonical check at write time; here we only enforce length so
    # absurd 4000-char inputs don't sneak through.
    preview_slug: str | None = Field(default=None, max_length=63)
    strip_prefix: bool | None = None

    # Upstream selection (which container Caddy proxies to).
    primary_service: str | None = None
    primary_port: int | None = Field(default=None, ge=1, le=65535)
    upstream_scheme: Literal["http", "https"] | None = None
    upstream_host_header: str | None = None
    upstream_tls_insecure: bool | None = None

    # Deploy behaviour
    build: bool | None = None

    @field_validator("compose_paths")
    @classmethod
    def _strip_empty_paths(cls, v: list[str]) -> list[str]:
        return [p for p in (s.strip() for s in v) if p]


# ────────────────────────────────────────── remote_ssh ──


class RemoteSshTargetConfig(_BaseTargetConfig):
    """`kind="remote_ssh"` — `docker compose` on a remote host
    reached over SSH. The deploy orchestrator side of this kind is
    not yet wired (Phase H out-of-scope), but the form needs to be
    valid so the operator can pre-stage the config."""

    host: str = Field(min_length=1, max_length=255)
    user: str = Field(default="deploy", min_length=1, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)
    # ULID of a Secret containing the SSH private key. Optional —
    # an agent-forwarded key on the GAPT host is allowed too.
    key_secret_ref: str | None = None
    compose_path: str = Field(default="docker-compose.yml", max_length=1024)


# ───────────────────────────────────────────── webhook ──


class WebhookTargetConfig(_BaseTargetConfig):
    """`kind="webhook"` — POST a payload to an external URL when a
    release is cut. Used to hand off to a CI/CD pipeline the user
    runs elsewhere (Vercel hooks, etc.)."""

    url: HttpUrl
    # ULID of a Secret containing an HMAC signing key the receiver
    # uses to verify the payload's authenticity.
    secret_ref: str | None = None
    # Names of project secrets to copy into the webhook payload body.
    env_keys: list[str] = Field(default_factory=list)

    @field_validator("env_keys")
    @classmethod
    def _dedup_env_keys(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for k in v:
            stripped = k.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                out.append(stripped)
        return out


# ────────────────────────────────────────── dispatcher ──


_KIND_TO_MODEL: dict[enums.DeployTargetKind, type[_BaseTargetConfig]] = {
    enums.DeployTargetKind.LOCAL: LocalTargetConfig,
    enums.DeployTargetKind.REMOTE_SSH: RemoteSshTargetConfig,
    enums.DeployTargetKind.WEBHOOK: WebhookTargetConfig,
}


def validate_target_config(
    kind: enums.DeployTargetKind, raw: dict[str, Any] | None
) -> dict[str, Any]:
    """Validate `raw` against the schema for `kind` and return a
    cleaned dict ready for DB storage.

    Raises:
      - `KindNotSupportedError` when no schema is registered for the
        kind (K8S today; the dispatcher above is the single point
        of truth for "which kinds are deployable from GAPT").
      - `TargetConfigInvalidError` on any pydantic validation
        failure, carrying the field-level errors so the API layer
        can return a structured 422.
    """
    model = _KIND_TO_MODEL.get(kind)
    if model is None:
        raise KindNotSupportedError(kind)
    payload = raw or {}
    try:
        instance = model.model_validate(payload)
    except ValidationError as exc:
        # `errors()` already yields the canonical pydantic shape —
        # we forward as-is so the frontend can show per-field
        # messages without re-parsing.
        raise TargetConfigInvalidError(
            f"deploy_target_config for kind={kind.value!r} failed validation",
            fields=[
                {
                    "loc": list(err["loc"]),
                    "msg": err["msg"],
                    "type": err["type"],
                }
                for err in exc.errors()
            ],
        ) from exc
    # `mode="json"` so HttpUrl etc. serialize to plain strings the
    # JSONB column understands. `exclude_none=True` keeps the saved
    # blob compact — None means "use the deploy default", not "store
    # the literal null".
    return instance.model_dump(mode="json", exclude_none=True)
