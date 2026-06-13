"""InvokeRequest / InvokeAttachment pydantic validation — pure model
tests, no app or DB.

The chat composer posts arbitrary client JSON into `POST
/sessions/{sid}/invoke`. The router's request model is the first and
only guard that obviously-wrong image payloads hit, so its constraints
are exercised here directly against the pydantic types:

- `media_type` is a closed allowlist (png/jpeg/gif/webp) — svg and
  every other type is rejected at the transport boundary.
- `data_base64` caps at 10M chars (~7.5 MB raw) so an oversized blob
  never reaches geny-executor's normalizer.
- `attachments` caps at 6 per turn.
- a turn must carry text or attachments — an empty message with no
  attachments is a no-op the model rejects, while an image-only turn
  (empty text + one image) is legitimate.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gapt_server.routers.sessions import InvokeAttachment, InvokeRequest

_MAX_B64 = 10_000_000


def _valid_attachment(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "image",
        "media_type": "image/png",
        "data_base64": "aGVsbG8=",
    }
    base.update(overrides)
    return base


def test_media_type_svg_rejected() -> None:
    """image/svg+xml is outside the closed media_type allowlist."""
    with pytest.raises(ValidationError):
        InvokeAttachment(
            kind="image",
            media_type="image/svg+xml",  # type: ignore[arg-type]
            data_base64="aGVsbG8=",
        )


def test_data_base64_over_cap_rejected() -> None:
    """One char past the 10M transport cap is rejected."""
    with pytest.raises(ValidationError):
        InvokeAttachment(
            kind="image",
            media_type="image/png",
            data_base64="a" * (_MAX_B64 + 1),
        )


def test_too_many_attachments_rejected() -> None:
    """Seven attachments exceed the per-turn cap of 6."""
    with pytest.raises(ValidationError):
        InvokeRequest(
            message="here are some images",
            attachments=[_valid_attachment() for _ in range(7)],
        )


def test_empty_message_no_attachments_rejected() -> None:
    """A turn with neither text nor attachments is a no-op the
    model_validator rejects."""
    with pytest.raises(ValidationError):
        InvokeRequest(message="   ")


def test_text_only_turn_valid() -> None:
    """Plain text with no attachments is the common, valid case."""
    req = InvokeRequest(message="hello world")

    assert req.message == "hello world"
    assert req.attachments is None


def test_image_only_turn_valid() -> None:
    """Empty message + a single image attachment is a legitimate turn
    (the validator only fires when both are absent)."""
    req = InvokeRequest(
        message="",
        attachments=[InvokeAttachment(**_valid_attachment())],  # type: ignore[arg-type]
    )

    assert req.message == ""
    assert req.attachments is not None
    assert len(req.attachments) == 1
    assert req.attachments[0].media_type == "image/png"
