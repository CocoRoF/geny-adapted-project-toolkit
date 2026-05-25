"""Single-user auth principal — MinIO/Jenkins-style.

GAPT runs as a solo self-hosted tool, so there is no User table and
no multi-user account system. Every authenticated request is the
same `AdminPrincipal`; the only thing that varies is whether the
caller was bound to a session cookie (USER actor) or came in through
an agent path (AGENT_SESSION actor).

The id is sourced from `settings.admin_id` so audit rows show
whatever identifier the operator configured (default `admin`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdminPrincipal:
    """Whatever the configured admin id is — surfaced on every
    authenticated request. Routers pass this to domain services in
    place of the old `models.User` row."""

    id: str
    # `display_name` is purely cosmetic — the IDE shows it in the
    # header. We default it to the id so a brand-new operator sees
    # "admin" without configuring a second env var.
    display_name: str | None = None
