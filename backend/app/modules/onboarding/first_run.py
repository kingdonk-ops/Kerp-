"""``python -m app.modules.onboarding.first_run``: first-run provisioning (T-0023).

Run once per deployment, after the migrations, as the runtime role (``KERP_DATABASE_URL``):

    python -m app.modules.onboarding.first_run --admin-email admin@company.example

The e-mail may come from ``KERP_FIRST_ADMIN_EMAIL`` instead. There is no password option: the
account is created without credentials. The command prints ``KERP_DEFAULT_TENANT_ID=<id>``
for the API and worker configuration. Exit codes: ``0`` done (or nothing to do), ``1``
refused (nothing written), ``2`` bad usage or configuration.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import uuid
from collections.abc import Sequence
from typing import Final, TextIO

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.db import Database, import_all_models
from app.core.module_loader.loader import discover_modules
from app.modules.onboarding import service
from app.modules.onboarding.schemas import DEMO_PROJECT_CODE, FirstRunRequest, FirstRunResult

EMAIL_ENV: Final = "KERP_FIRST_ADMIN_EMAIL"
EXIT_REFUSED: Final = 1
EXIT_USAGE: Final = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.onboarding.first_run",
        description=(
            "Provision this deployment: its one tenant, the deployment settings and the first"
            " System admin (no password: SSO or a one-time link). Safe to re-run."
        ),
    )
    parser.add_argument(
        "--admin-email", help=f"the first System admin's e-mail (default: ${EMAIL_ENV})"
    )
    parser.add_argument("--admin-name", default="", help="the admin's display name")
    parser.add_argument(
        "--tenant-id",
        type=uuid.UUID,
        help="the tenant id to create (default: $KERP_DEFAULT_TENANT_ID, else a new one)",
    )
    parser.add_argument(
        "--tenant-name", help="internal name of the hidden tenant (never shown to users)"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            f"development only: also grant create_projects and create the synthetic demo"
            f" project {DEMO_PROJECT_CODE} with the admin as PER-09"
        ),
    )
    return parser


def load_modules() -> None:
    """Import every model and module service, as the API does at start-up, so the mappers
    are complete and the modules' listeners (e.g. a new project's rule sets) are registered."""
    import_all_models()
    registry = discover_modules()
    for manifest in registry:
        importlib.import_module(f"{registry.package}.{manifest.name}.service")


def _report(result: FirstRunResult, email: str, out: TextIO) -> None:
    def state(created: bool) -> str:
        return "created" if created else "present"

    out.write(f"KERP_DEFAULT_TENANT_ID={result.tenant_id}\n")
    out.write(f"tenant: {state(result.tenant_created)}\n")
    out.write(f"deployment settings: {state(result.settings_created)}\n")
    internal = "internal" if result.admin_is_internal else "not internal"
    out.write(f"system admin account: {state(result.admin_created)} ({email}, {internal})\n")
    out.write(f"system_admin grant: {'granted' if result.admin_granted else 'present'}\n")
    if result.demo_project_id is not None:
        out.write(f"demo project {DEMO_PROJECT_CODE}: {state(result.demo_project_created)}\n")
    out.write(
        "Set KERP_DEFAULT_TENANT_ID to the value above for the API and the worker. No password"
        " was set: the System admin signs in with Microsoft (Entra ID SSO links the account by"
        " e-mail) or sets a password through a one-time link.\n"
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
    args = _parser().parse_args(argv)
    settings = settings or get_settings()
    email = args.admin_email or os.environ.get(EMAIL_ENV, "")
    if not email:
        err.write(f"give --admin-email or set {EMAIL_ENV}\n")
        return EXIT_USAGE
    if not settings.database_url:
        err.write("KERP_DATABASE_URL must be set\n")
        return EXIT_USAGE
    fields: dict[str, object] = {
        "admin_email": email,
        "admin_display_name": args.admin_name,
        "tenant_id": args.tenant_id,
        "demo": args.demo,
    }
    if args.tenant_name is not None:
        fields["tenant_display_name"] = args.tenant_name
    try:
        request = FirstRunRequest.model_validate(fields)
    except ValidationError as exc:
        names = ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]}))
        err.write(f"invalid input: {names}\n")
        return EXIT_USAGE
    load_modules()
    database = Database(settings.database_url)
    try:
        result = service.first_run(database, request, settings=settings)
    except service.DemoNotAllowedError as exc:
        err.write(f"{exc}\n")
        return EXIT_USAGE
    except service.FirstRunError as exc:
        err.write(f"refused: {exc}\n")
        return EXIT_REFUSED
    finally:
        database.dispose()
    _report(result, request.admin_email, out)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main(sys.argv[1:]))
