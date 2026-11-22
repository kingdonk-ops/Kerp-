"""The app-wide 503 handlers (T-0349, TD-290, TD-525).

A revocation-store outage is one answer everywhere: ``503`` with
``{"detail": AUTH_UNAVAILABLE}``, whichever endpoint or dependency hit it. A raw Redis
connection or timeout error that escapes a caller is ``503`` with ``{"detail":
REDIS_UNAVAILABLE}`` instead of a generic 500. Any other Redis error is a bug and stays 500.
"""

from collections.abc import Callable

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.revocation import (
    AUTH_UNAVAILABLE,
    REDIS_UNAVAILABLE,
    RevocationUnavailableError,
)
from app.main import create_app
from tests.auth_support import make_settings

PATH = "/__test__/outage"


def _app_raising(error: Callable[[], Exception]) -> FastAPI:
    app = create_app(make_settings())

    def outage() -> None:
        raise error()

    app.add_api_route(PATH, outage)
    return app


def test_a_revocation_outage_anywhere_is_the_one_503() -> None:
    app = _app_raising(lambda: RevocationUnavailableError("synthetic outage"))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(PATH)
    assert (response.status_code, response.json()) == (503, {"detail": AUTH_UNAVAILABLE})
    assert response.headers["x-request-id"]  # still inside the request-context middleware


@pytest.mark.parametrize(
    "error",
    [
        lambda: redis.ConnectionError("synthetic: connection refused"),
        lambda: redis.TimeoutError("synthetic: timed out"),
    ],
    ids=["connection", "timeout"],
)
def test_a_raw_redis_outage_is_503_not_500(error: Callable[[], Exception]) -> None:
    with TestClient(_app_raising(error), raise_server_exceptions=False) as client:
        response = client.get(PATH)
    assert (response.status_code, response.json()) == (503, {"detail": REDIS_UNAVAILABLE})
    assert "synthetic" not in response.text  # never the driver's message (it can name a host)


def test_any_other_redis_error_stays_a_500() -> None:
    app = _app_raising(lambda: redis.ResponseError("synthetic: WRONGTYPE"))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(PATH)
    assert response.status_code == 500
    assert "synthetic" not in response.text
