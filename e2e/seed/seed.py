"""Dev-only seed of the e2e suite (T-0027): ``python e2e/seed/seed.py --run <id>``.

Run by the Playwright ``globalSetup`` after ``alembic upgrade head`` and
``python -m app.modules.onboarding.first_run --demo``, with ``backend/`` on ``PYTHONPATH``
and the API's environment (``KERP_DATABASE_URL`` as the runtime role,
``KERP_DEFAULT_TENANT_ID``). It refuses to run unless ``KERP_ENVIRONMENT=development``.

Given the run id it, in one tenant-level unit of work bound as a System admin:

1. sets the first-run System admin's password to ``$KERP_E2E_ADMIN_PASSWORD`` (first-run
   creates the account without credentials, by design);
2. creates this run's QA user ``e2e-qa-<run>@example.test`` (a member of nothing) unless it
   exists, and sets its password to ``$KERP_E2E_QA_PASSWORD``;
3. creates the tenant-layer ``check`` rule set ``e2e_smoke``, owned by no module, unless it
   exists, so every project created afterwards gets a copy (ADR 0004 §2).

Everything goes through the owning module's ``service.py`` (the identity module's
``app/core/auth/service.py``, ``admin``, ``validation``, ``projects``); no table is written
directly. Synthetic data only. It prints one JSON object (ids the journey needs) on stdout.

Exit codes: ``0`` done, ``1`` refused (first-run not done; nothing written), ``2`` bad usage
or environment (not development, a password missing or refused by the policy).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final, TextIO

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.db import Database

EXIT_REFUSED: Final = 1
EXIT_USAGE: Final = 2
ADMIN_PASSWORD_ENV: Final = "KERP_E2E_ADMIN_PASSWORD"
QA_PASSWORD_ENV: Final = "KERP_E2E_QA_PASSWORD"
RULE_SET_KEY: Final = "e2e_smoke"
RUN_PATTERN: Final = re.compile(r"[a-z0-9]{1,24}")
QA_EMAIL: Final = "e2e-qa-{run}@example.test"
QA_NAME: Final = "Quinn Example {run}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python e2e/seed/seed.py",
        description="Development only: synthetic users and rule set for the e2e journey.",
    )
    parser.add_argument("--run", required=True, help="this run's id: 1-24 of [a-z0-9]")
    parser.add_argument("--admin-email", required=True, help="the first-run System admin")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    environ = os.environ if environ is None else environ
    args = _parser().parse_args(argv)
    # Checked before the app is configured: production settings are never even loaded.
    environment = settings.environment if settings else environ.get("KERP_ENVIRONMENT")
    if environment != "development":
        err.write("refused: the e2e seed runs only with KERP_ENVIRONMENT=development\n")
        return EXIT_USAGE
    if not RUN_PATTERN.fullmatch(args.run):
        err.write("--run must be 1-24 characters of [a-z0-9]\n")
        return EXIT_USAGE
    passwords = {name: environ.get(name, "") for name in (ADMIN_PASSWORD_ENV, QA_PASSWORD_ENV)}
    missing = [name for name, value in passwords.items() if not value]
    if missing:
        err.write(f"set {', '.join(missing)}\n")
        return EXIT_USAGE

    from app.core.config import get_settings
    from app.core.db import Database
    from app.modules.onboarding.first_run import load_modules

    settings = settings or get_settings()
    if not settings.database_url or settings.default_tenant_id is None:
        err.write("KERP_DATABASE_URL and KERP_DEFAULT_TENANT_ID must be set\n")
        return EXIT_USAGE
    load_modules()
    database = Database(settings.database_url)
    try:
        return _seed(database, settings, args.run, args.admin_email, passwords, out, err)
    finally:
        database.dispose()


def _seed(
    database: Database,
    settings: Settings,
    run: str,
    admin_email: str,
    passwords: Mapping[str, str],
    out: TextIO,
    err: TextIO,
) -> int:
    from app.core.auth import service as identity
    from app.core.auth.password_policy import PasswordPolicyError
    from app.core.tenancy.context import TenancyContext
    from app.modules.admin import service as admin
    from app.modules.admin.schemas import UserCreate
    from app.modules.onboarding.schemas import DEMO_PROJECT_CODE
    from app.modules.projects import service as projects
    from app.modules.validation import service as rule_store

    tenant_id = settings.default_tenant_id
    assert tenant_id is not None
    context = TenancyContext(tenant_id=tenant_id, is_system_admin=True)
    qa_email = QA_EMAIL.format(run=run)
    current = ADMIN_PASSWORD_ENV  # the password being set, named in a policy refusal
    try:
        with database.unit_of_work(context) as session:
            admin_user = identity.find_user_by_email(session, admin_email)
            if admin_user is None or not admin_user.is_active:
                err.write(f"refused: no active account {admin_email}: run first_run first\n")
                return EXIT_REFUSED
            identity.set_user_password(
                session, admin_user.id, passwords[ADMIN_PASSWORD_ENV], settings=settings
            )
            qa = identity.find_user_by_email(session, qa_email)
            if qa is None:
                data = UserCreate(email=qa_email, display_name=QA_NAME.format(run=run))
                qa = admin.create_user(session, data, actor_id=admin_user.id)
            current = QA_PASSWORD_ENV
            identity.set_user_password(
                session, qa.id, passwords[QA_PASSWORD_ENV], settings=settings
            )
            if not any(s.key == RULE_SET_KEY for s in rule_store.list_rule_sets(session, "tenant")):
                rule_store.create_rule_set(
                    session,
                    key=RULE_SET_KEY,
                    kind="check",
                    layer="tenant",
                    actor_id=admin_user.id,
                    source="system",
                )
            demo = next(
                (
                    p.id
                    for p in projects.list_projects(session, projects.live_project_ids(session))
                    if p.code == DEMO_PROJECT_CODE
                ),
                None,
            )
            state = {
                "tenant_id": str(tenant_id),
                "admin_user_id": str(admin_user.id),
                "qa_user_id": str(qa.id),
                "qa_email": qa_email,
                "qa_name": qa.display_name,
                "demo_project_id": str(demo) if demo else None,
                "rule_set_key": RULE_SET_KEY,
            }
    except PasswordPolicyError as exc:
        err.write(f"{current} is refused by the password policy ({exc})\n")
        return EXIT_USAGE
    out.write(json.dumps(state, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main(sys.argv[1:]))
