"""Shared test fixtures.

Unit tests must never touch real AWS -- S3/DynamoDB calls are mocked
via `moto` (mirrors crawler-service/parser-service's
`tests/conftest.py`). Real Postgres access is exercised separately in
verification scripts, never in this unit-test suite: route-level tests
fake out the DB dependency entirely (see `tests/test_domains.py`).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import routes
from app import app


@pytest.fixture(autouse=True)
def _isolated_aws_env(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    yield


def _fake_db_connection():
    """Stands in for `_get_db_connection` in route-level tests -- the
    DB layer itself is faked (`tests/test_domains.py` monkeypatches
    `routes.fetch_domain` directly), so this never needs to be a real
    psycopg2 connection; it only has to satisfy the dependency's
    generator shape.
    """
    yield None


@pytest.fixture
def client_no_rate_limit():
    """A TestClient with the rate-limit dependency short-circuited and
    the real DB connection dependency swapped for a no-op stand-in --
    for tests that exercise domain lookup/history behavior, not the
    rate limiter itself (that's `tests/test_ratelimit.py`) and never
    against a real Postgres (that's the localstack/local-Postgres
    verification run, not this unit-test suite).
    """
    app.dependency_overrides[routes.enforce_rate_limit] = lambda: None
    app.dependency_overrides[routes._get_db_connection] = _fake_db_connection
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(routes.enforce_rate_limit, None)
        app.dependency_overrides.pop(routes._get_db_connection, None)
