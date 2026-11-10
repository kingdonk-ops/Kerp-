"""The ``project_module_setting`` table: one row per project per toggled module (T-0011).

A project without a row for a module uses the manifest's ``default_enabled``. The row is
project-scoped (``project_id`` NOT NULL), so project RLS (T-0007) confines it to its project.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, BaseRecordMixin

TABLE: Final = "project_module_setting"


class ProjectModuleSetting(Base, BaseRecordMixin):
    """Whether ``module_name`` is switched on for the row's project."""

    __tablename__ = TABLE
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "project_id", "module_name", name="uq_project_module_setting_module"
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    module_name: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
