"""ULID helpers — primary-key generator used across every ORM model.

Why ULID, not UUID v4: ULIDs are lexicographically sortable by creation
time, which lets `audit_events` paginate by primary key without a
secondary index on `ts`. UUIDv7 would also work — picked ULID because
the python-ulid package is already in `pyproject.toml`.

Wire format: 26-character Crockford base32 (`text` column in Postgres).
"""

from __future__ import annotations

from ulid import ULID

__all__ = ["new_ulid", "ulid_default"]


def new_ulid() -> str:
    """Return a fresh 26-character ULID string."""
    return str(ULID())


def ulid_default() -> str:
    """Callable for SQLAlchemy `default=` — same as `new_ulid` but the
    explicit name makes intent obvious in column definitions."""
    return new_ulid()
