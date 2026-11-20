"""``python -m app.modules.teams.reconcile``: the teams reconcile command (T-0075, ADR 0008).

Compares the team-derived grants in the core membership store with the teams tables and
prints every drift (missing, excess, orphan link ids, ``sod_pending``):

    python -m app.modules.teams.reconcile                     # report only
    python -m app.modules.teams.reconcile --fix               # repair (never confirms SoD)
    python -m app.modules.teams.reconcile --fix --confirm-sod # also re-add sod_pending tuples

Runs as the runtime role (``KERP_DATABASE_URL``) in a tenant-level ``system_admin`` session of
``--tenant-id`` (default ``KERP_DEFAULT_TENANT_ID``); the repairs are audited with no actor and
``reason: reconcile``. Exit codes: ``0`` no drift left, ``1`` drift found and not (fully)
fixed, ``2`` bad usage or configuration.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from typing import Final, TextIO

from app.core.config import Settings, get_settings
from app.core.db import Database, import_all_models
from app.core.tenancy.context import TenancyContext
from app.modules.teams import service
from app.modules.teams.service import DerivedTuple, LinkDrift, ReconcileReport

EXIT_DRIFT: Final = 1
EXIT_USAGE: Final = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.teams.reconcile",
        description=(
            "Compare the team-derived project grants with the teams tables and report missing,"
            " excess and orphan grants; --fix repairs them."
        ),
    )
    parser.add_argument("--fix", action="store_true", help="repair the drift found")
    parser.add_argument(
        "--confirm-sod",
        action="store_true",
        help=(
            "with --fix: also re-add missing grants that combine a hold-authority persona with"
            " an individual PER-09 (audited sod_warning: true)"
        ),
    )
    parser.add_argument(
        "--tenant-id", type=uuid.UUID, help="the tenant (default: $KERP_DEFAULT_TENANT_ID)"
    )
    return parser


def _tuple(kind: str, value: DerivedTuple) -> str:
    user_id, project_id, persona, site_node_id = value
    site = f" site {site_node_id}" if site_node_id else ""
    return f"  {kind} {user_id} {project_id} {persona}{site}\n"


def _link(drift: LinkDrift, out: TextIO) -> None:
    out.write(f"link {drift.link_id}{' (orphan)' if drift.orphan else ''}\n")
    for kind, values in (
        ("missing", drift.missing),
        ("excess", drift.excess),
        ("sod_pending", drift.sod_pending),
    ):
        for value in sorted(values, key=str):
            out.write(_tuple(kind, value))
    if drift.repaired:
        out.write(f"  repaired: added {len(drift.added)}, removed {len(drift.removed)}\n")


def _report(report: ReconcileReport, out: TextIO) -> None:
    if not report.drift:
        out.write("no drift\n")
        return
    for drift in report.links:
        _link(drift, out)
    if not report.fixed:
        out.write("drift not fixed: run with --fix to repair it\n")
    elif report.unresolved:
        out.write(
            "sod_pending grants were not re-added: check the PER-09 combination and run with"
            " --fix --confirm-sod to confirm it\n"
        )


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    parser = _parser()
    args = parser.parse_args(argv)
    if args.confirm_sod and not args.fix:
        parser.error("--confirm-sod needs --fix")
    settings = settings or get_settings()
    if not settings.database_url:
        err.write("KERP_DATABASE_URL must be set\n")
        return EXIT_USAGE
    tenant_id = args.tenant_id or settings.default_tenant_id
    if tenant_id is None:
        err.write("give --tenant-id or set KERP_DEFAULT_TENANT_ID\n")
        return EXIT_USAGE
    import_all_models()
    database = Database(settings.database_url)
    context = TenancyContext(tenant_id=tenant_id, is_system_admin=True)
    try:
        with database.unit_of_work(context) as session:
            report = service.reconcile(session, fix=args.fix, confirm_sod=args.confirm_sod)
    finally:
        database.dispose()
    _report(report, out)
    return EXIT_DRIFT if report.unresolved else 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main(sys.argv[1:]))
