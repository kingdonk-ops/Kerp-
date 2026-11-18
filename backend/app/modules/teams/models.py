"""Teams tables (T-0013, ADR 0008, diff §4.1 Teams).

Three tenant-level tables (``project_id IS NULL``, CHECK), all ``BaseRecordMixin`` and under
the ``team`` RLS policy (migration ``0028``, ``app.core.tenancy.rls``): a ``system_admin``
session reads and writes every row; any other session reads a team, its members and its
links only when the team is linked to a project the session describes (bound, allowed or
member project) or the caller is a live member; nobody else writes.

* ``oe_teams_team``: ``name`` (unique per tenant among live teams, case-insensitively, via
  the normalised ``name_key``), ``description``.
* ``oe_teams_member``: one live row per (team, user account).
* ``oe_teams_project_link``: one live row per (team, project) with the persona every member
  gets there (PER-01..PER-14 except PER-09, plus PER-16); ``site_node_id`` exactly for PER-07.

Removing anything soft-deletes it. ``linked_project_id`` has no foreign key: the project row
belongs to the projects module (CLAUDE.md §6), like ``auth_project_membership``.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.auth.team_grants import LINK_PERSONAS
from app.core.db import GUID, Base, BaseRecordMixin
from app.core.tenancy.rls import RlsPolicy, rls_table_info

TEAM_TABLE: Final = "oe_teams_team"
MEMBER_TABLE: Final = "oe_teams_member"
LINK_TABLE: Final = "oe_teams_project_link"
TABLES: Final = (TEAM_TABLE, MEMBER_TABLE, LINK_TABLE)
NAME_INDEX: Final = "uq_oe_teams_team_name_live"
MEMBER_INDEX: Final = "uq_oe_teams_member_live"
LINK_INDEX: Final = "uq_oe_teams_project_link_live"
NAME_MAX: Final = 120
DESCRIPTION_MAX: Final = 1000
# PER-09 and PER-15 are individual assignments only (TD-251): never a link role.
LINK_PERSONA_CODES: Final = tuple(sorted(p.value for p in LINK_PERSONAS))
SITE_PERSONA: Final = "PER-07"
_LIVE: Final = "deleted_at IS NULL"


def _policy(team_column: str) -> dict[str, object]:
    return rls_table_info(
        RlsPolicy("team", team_column=team_column, link_table=LINK_TABLE, member_table=MEMBER_TABLE)
    )


def _live_unique(name: str, *columns: str) -> Index:
    return Index(
        name, *columns, unique=True, postgresql_where=text(_LIVE), sqlite_where=text(_LIVE)
    )


def normalize_name(name: str) -> str:
    """The case-insensitive key of a team name (``name_key``): whitespace runs collapsed,
    case folded."""
    return " ".join(name.split()).casefold()


class Team(Base, BaseRecordMixin):
    """A named group of user accounts of the tenant (access only, not a crew or roster)."""

    __tablename__ = TEAM_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        _live_unique(NAME_INDEX, "tenant_id", "name_key"),
        _policy("id"),
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX), nullable=False)
    name_key: Mapped[str] = mapped_column(String(NAME_MAX), nullable=False)
    description: Mapped[str] = mapped_column(String(DESCRIPTION_MAX), nullable=False, default="")


class TeamMember(Base, BaseRecordMixin):
    """A user account in a team. Kept while the account is deactivated (ADR 0008)."""

    __tablename__ = MEMBER_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        _live_unique(MEMBER_INDEX, "tenant_id", "team_id", "user_id"),
        _policy("team_id"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{TEAM_TABLE}.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("auth_user.id"), nullable=False, index=True
    )


class TeamProjectLink(Base, BaseRecordMixin):
    """A team linked to a project with one persona: every member gets it there."""

    __tablename__ = LINK_TABLE
    __table_args__ = (
        CheckConstraint("project_id IS NULL", name="tenant_level"),
        CheckConstraint(
            "persona IN (" + ", ".join(f"'{p}'" for p in LINK_PERSONA_CODES) + ")",
            name="persona",
        ),
        CheckConstraint(f"(persona = '{SITE_PERSONA}') = (site_node_id IS NOT NULL)", name="site"),
        _live_unique(LINK_INDEX, "tenant_id", "team_id", "linked_project_id"),
        _policy("team_id"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey(f"{TEAM_TABLE}.id"), nullable=False, index=True
    )
    linked_project_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    persona: Mapped[str] = mapped_column(String(6), nullable=False)
    site_node_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
