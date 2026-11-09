"""Shared database test helpers: the target URL and an isolated test model.

The suite runs on a throwaway SQLite file by default. CI's PostgreSQL step sets
``KERP_DATABASE_URL`` and the same tests run against PostgreSQL 16.
"""

import os
from pathlib import Path

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.db import NAMING_CONVENTION, BaseRecordMixin

POSTGRES_URL = os.environ.get("KERP_DATABASE_URL", "")
ON_POSTGRES = POSTGRES_URL.startswith("postgresql")


def database_url(tmp_path: Path) -> str:
    if ON_POSTGRES:
        return POSTGRES_URL
    return "sqlite:///" + (tmp_path / "kerp.db").as_posix()


class ScratchBase(DeclarativeBase):
    """Separate metadata, so test tables never leak into ``Base.metadata`` / migrations."""


ScratchBase.metadata.naming_convention = NAMING_CONVENTION


class Widget(ScratchBase, BaseRecordMixin):
    __tablename__ = "test_widget"

    name: Mapped[str] = mapped_column(String(50), nullable=False)
