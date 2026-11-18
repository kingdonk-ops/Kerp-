"""Onboarding API, mounted at ``/api/v1/onboarding`` (T-0023): no endpoints.

First-run provisioning is a deployment step run from the command line
(``python -m app.modules.onboarding.first_run``), never over HTTP: before it has run there is
no System admin who could authorise a request.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["onboarding"])
