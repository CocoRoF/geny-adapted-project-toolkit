"""Phase N.2.2 — preset registry contract.

Pure unit tests — no DB, no HTTP. Verifies the registration
side-effects, option validation, and the deterministic listing order
the wizard depends on.
"""

from __future__ import annotations

import pytest

from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode
from gapt_server.domains.scaffolds.registry import (
    ScaffoldOption,
    all_presets,
    get_preset,
)


def test_all_five_presets_are_registered() -> None:
    ids = {p.id for p in all_presets()}
    assert ids == {
        "empty",
        "fullstack_fastapi_nextjs",
        "backend_fastapi",
        "frontend_nextjs",
        "static_vite",
    }


def test_preset_listing_order_is_deterministic_and_empty_is_last() -> None:
    """The wizard's card grid order matches `all_presets()` so the
    user's eye lands on the "real stack" cards first. Empty stays
    LAST per `presets/__init__.py` import order."""
    ids = [p.id for p in all_presets()]
    assert ids[-1] == "empty"
    # Order matches the documented sequence.
    assert ids == [
        "fullstack_fastapi_nextjs",
        "backend_fastapi",
        "frontend_nextjs",
        "static_vite",
        "empty",
    ]


def test_get_preset_returns_match_for_known_id() -> None:
    p = get_preset("backend_fastapi")
    assert p.id == "backend_fastapi"
    assert "FastAPI" in p.stack


def test_get_preset_raises_preset_unknown_on_bogus_id() -> None:
    with pytest.raises(ScaffoldError) as exc:
        get_preset("does_not_exist")
    assert exc.value.code is ScaffoldErrorCode.PRESET_UNKNOWN


def test_summary_dict_is_json_serialisable() -> None:
    import json  # noqa: PLC0415

    for p in all_presets():
        # If any field were e.g. a tuple containing a non-serialisable
        # object, this raises. The wizard caches the listing client-side,
        # so the shape must round-trip cleanly through fetch/JSON.
        json.dumps(p.to_summary_dict())


def test_option_validate_integer_coerces_strings() -> None:
    opt = ScaffoldOption(id="p", label="P", type="integer", default=80)
    assert opt.validate("3000") == 3000
    assert opt.validate(8000) == 8000
    assert opt.validate(None) == 80


def test_option_validate_integer_enforces_bounds() -> None:
    opt = ScaffoldOption(
        id="p", label="P", type="integer", default=80, min_value=1, max_value=65535
    )
    with pytest.raises(ScaffoldError) as exc:
        opt.validate(0)
    assert exc.value.code is ScaffoldErrorCode.OPTION_INVALID

    with pytest.raises(ScaffoldError):
        opt.validate(99999)


def test_option_validate_enum_rejects_off_list_value() -> None:
    opt = ScaffoldOption(
        id="db", label="DB", type="enum", default="none", choices=("none", "postgres")
    )
    assert opt.validate("postgres") == "postgres"
    with pytest.raises(ScaffoldError) as exc:
        opt.validate("mysql")
    assert exc.value.code is ScaffoldErrorCode.OPTION_INVALID


def test_option_validate_boolean_accepts_str_lower_case() -> None:
    """The wizard ships JSON; that means booleans always come in as
    actual JSON true/false. Tolerant string handling is for hand-crafted
    API clients (curl + bash)."""
    opt = ScaffoldOption(id="tw", label="TW", type="boolean", default=True)
    assert opt.validate(True) is True
    assert opt.validate("false") is False


def test_preset_validate_options_rejects_unknown_keys() -> None:
    preset = get_preset("frontend_nextjs")
    with pytest.raises(ScaffoldError) as exc:
        preset.validate_options({"with_tailwind": True, "what_is_this": "?"})
    assert exc.value.code is ScaffoldErrorCode.OPTION_INVALID


def test_preset_validate_options_applies_defaults() -> None:
    preset = get_preset("frontend_nextjs")
    cleaned = preset.validate_options({})  # nothing supplied
    assert cleaned == {"primary_port": 3000, "with_tailwind": True}


def test_empty_preset_has_no_compose_in_defaults() -> None:
    """Per Phase N plan §3.1 the empty preset has no docker compose
    config (dev/prod 미사용). Verifies the deploy_target_defaults dict
    is empty so the new GAPT project row doesn't carry phantom
    compose pointers."""
    empty = get_preset("empty")
    assert empty.deploy_target_defaults == {}
    assert empty.option_schema == ()
