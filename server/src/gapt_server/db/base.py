"""SQLAlchemy 2.x declarative base shared by every ORM model.

A separate module so `alembic env.py` and the model definitions both
import `Base` from the same place without circular-import grief.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Consistent constraint naming makes alembic autogenerate produce stable
# DDL (otherwise it emits constraint names like `uq_…` with random suffixes
# that churn diffs).
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
