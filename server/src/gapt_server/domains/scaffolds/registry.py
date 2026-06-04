"""Phase N — ScaffoldPreset registry.

A preset is the contract between the wizard's preset card and the
files that land in the new repo's first commit.

  * ``id`` is the stable string the API uses
  * ``display_name`` / ``description`` / ``stack`` feed the card UI
  * ``option_schema`` drives the wizard's Step 3 dynamic form
  * ``deploy_target_defaults`` are merged into the new GAPT project
    row so the freshly-bound workspace inherits the right
    compose_path / primary_service / preview_mode
  * ``render(ctx)`` is the function that turns the validated context
    into the in-memory file tree the pusher will commit

Presets are registered eagerly by importing
``gapt_server.domains.scaffolds.presets`` — each module appends to
``_REGISTERED`` so the order is deterministic + matches the listing
endpoint output.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from gapt_server.domains.scaffolds.context import RenderContext
from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode

OptionType = Literal["integer", "string", "boolean", "enum"]


@dataclass(frozen=True)
class ScaffoldOption:
    """Declarative description of one wizard form field.

    The frontend renders this verbatim — option_schema is the contract
    between the server's preset and the dynamic form. Keep it small
    and serialisation-friendly so listing endpoint output stays
    cacheable.
    """

    id: str
    label: str
    type: OptionType
    default: Any = None
    description: str = ""
    # Used when type == "enum".
    choices: tuple[str, ...] = ()
    # Used when type == "integer". Inclusive.
    min_value: int | None = None
    max_value: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "description": self.description,
        }
        if self.type == "enum":
            out["choices"] = list(self.choices)
        if self.type == "integer":
            if self.min_value is not None:
                out["min"] = self.min_value
            if self.max_value is not None:
                out["max"] = self.max_value
        return out

    def validate(self, value: Any) -> Any:
        """Coerce + validate a wizard-supplied value. Raises
        ``ScaffoldError(OPTION_INVALID)`` on mismatch so the upstream
        router can 422 with a clear field reference."""
        if value is None:
            return self.default
        if self.type == "integer":
            try:
                coerced = int(value)
            except (TypeError, ValueError) as exc:
                raise ScaffoldError(
                    ScaffoldErrorCode.OPTION_INVALID,
                    f"option {self.id!r} must be integer, got {value!r}",
                ) from exc
            if self.min_value is not None and coerced < self.min_value:
                raise ScaffoldError(
                    ScaffoldErrorCode.OPTION_INVALID,
                    f"option {self.id!r} must be ≥ {self.min_value}, got {coerced}",
                )
            if self.max_value is not None and coerced > self.max_value:
                raise ScaffoldError(
                    ScaffoldErrorCode.OPTION_INVALID,
                    f"option {self.id!r} must be ≤ {self.max_value}, got {coerced}",
                )
            return coerced
        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return value.lower() == "true"
            raise ScaffoldError(
                ScaffoldErrorCode.OPTION_INVALID,
                f"option {self.id!r} must be boolean, got {value!r}",
            )
        if self.type == "enum":
            if value in self.choices:
                return value
            raise ScaffoldError(
                ScaffoldErrorCode.OPTION_INVALID,
                f"option {self.id!r} must be one of {list(self.choices)}, got {value!r}",
            )
        # "string"
        if not isinstance(value, str):
            raise ScaffoldError(
                ScaffoldErrorCode.OPTION_INVALID,
                f"option {self.id!r} must be string, got {type(value).__name__}",
            )
        return value


RenderFn = Callable[[RenderContext], dict[str, bytes]]


@dataclass(frozen=True)
class ScaffoldPreset:
    """One opinionated starter stack the wizard can pick."""

    id: str
    display_name: str
    description: str
    # Free-form stack chips for the card: ["FastAPI", "Next.js", ...].
    stack: tuple[str, ...]
    # Iconography hint the frontend maps to a lucide icon.
    icon: str
    # Defaults merged into the new GAPT project row's
    # ``deploy_target_config``. Empty when the preset doesn't deploy.
    deploy_target_kind: str  # "local" | "remote_ssh" | "webhook"
    deploy_target_defaults: dict[str, Any] = field(default_factory=dict)
    option_schema: tuple[ScaffoldOption, ...] = ()
    render: RenderFn = field(default=lambda _: {})

    def validate_options(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Run every declared option's validate() and return the
        cleaned dict. Unknown keys raise OPTION_INVALID — preset
        authors keep the schema authoritative."""
        cleaned: dict[str, Any] = {}
        declared_ids = {o.id for o in self.option_schema}
        for option in self.option_schema:
            cleaned[option.id] = option.validate(raw.get(option.id))
        for k in raw:
            if k not in declared_ids:
                raise ScaffoldError(
                    ScaffoldErrorCode.OPTION_INVALID,
                    f"unknown option {k!r} for preset {self.id!r}",
                )
        return cleaned

    def to_summary_dict(self) -> dict[str, Any]:
        """Serialisable shape for the listing endpoint."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "stack": list(self.stack),
            "icon": self.icon,
            "deploy_target_kind": self.deploy_target_kind,
            "option_schema": [o.to_dict() for o in self.option_schema],
        }


# Registry — populated by `presets/__init__.py` import side-effects.
_REGISTERED: list[ScaffoldPreset] = []


def register(preset: ScaffoldPreset) -> None:
    if any(p.id == preset.id for p in _REGISTERED):
        raise RuntimeError(
            f"scaffold preset id {preset.id!r} already registered; "
            "preset modules must not double-import"
        )
    _REGISTERED.append(preset)


def all_presets() -> list[ScaffoldPreset]:
    # Eagerly import the presets package so any module that hasn't
    # been touched yet still appears in the registry. The import
    # has side effects (each preset module calls `register(...)`).
    import gapt_server.domains.scaffolds.presets  # noqa: F401  PLC0415

    return list(_REGISTERED)


def get_preset(preset_id: str) -> ScaffoldPreset:
    for preset in all_presets():
        if preset.id == preset_id:
            return preset
    raise ScaffoldError(
        ScaffoldErrorCode.PRESET_UNKNOWN,
        f"unknown preset id {preset_id!r}",
    )
