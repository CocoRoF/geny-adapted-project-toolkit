"""Database layer for gapt-server.

- `base.Base` — SQLAlchemy declarative base (all ORM models hang off it)
- `models` — the 11 control-plane tables from
  `docs/03_system_architecture.md` §3.3 + `docs/plan/m1/e1_backend_foundation.md` §1.1
- `enums` — strong enums for status/role/etc.
- `session` — async session factory (lifecycle is owned by `gapt_server.container`)
"""

from gapt_server.db import enums, models
from gapt_server.db.base import Base
from gapt_server.db.session import create_engine, create_session_factory

__all__ = ["Base", "create_engine", "create_session_factory", "enums", "models"]
