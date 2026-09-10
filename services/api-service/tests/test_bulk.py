"""`POST /v1/domains/bulk`: requires an active self_serve/licensing key
(missing/invalid/free-tier key -> clean 403, never a 500), returns a
per-domain found/not-found result (never a request-level 404), is
capped at BULK_MAX_DOMAINS, and is rate-limited by the calling key's
own tier via the same DynamoDB counter `tests/test_ratelimit.py`
exercises for the free tier.

DB access is faked (mirrors `tests/test_domains.py`/`tests/test_auth.py`);
only DynamoDB is real, via moto -- so this suite never touches a real
Postgres, matching this service's existing unit-test philosophy (see
`tests/conftest.py`).
"""

from __future__ import annotations

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from api import auth, routes
from api.config import BULK_MAX_DOMAINS
from app import app

RATE_LIMIT_TABLE = "agent-intel-bulk-test"


class _DummyConn:
    """Stands in for a real psycopg2 connection -- every test here
    monkeypatches the functions that would actually use it
    (`auth.fetch_api_key_by_hash`, `routes.fetch_domains_bulk`), so it
    only ever needs a `close()` method.
    """

    def close(self) -> None:
        pass


def _key_row(key_id: str = "key-1", plan_tier: str = "self_serve", rate_limit: int = 1000, active: bool = True) -> dict:
    return {"key_id": key_id, "plan_tier": plan_tier, "rate_limit": rate_limit, "active": active}


def _create_rate_limit_table(dynamodb_client) -> None:
    dynamodb_client.create_table(
        TableName=RATE_LIMIT_TABLE,
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


def test_bulk_with_no_api_key_header_is_a_clean_403_not_401_or_500():
    client = TestClient(app)

    response = client.post("/v1/domains/bulk", json={"domains": ["example.com"]})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_bulk_with_an_unrecognized_api_key_is_a_clean_403(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: None)

    client = TestClient(app)
    response = client.post(
        "/v1/domains/bulk", json={"domains": ["example.com"]}, headers={"X-API-Key": "ai_live_not-real"}
    )

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_bulk_with_a_valid_free_tier_key_is_a_clean_403(monkeypatch):
    """Free tier is the unauthenticated IP-limited path -- a key that
    somehow carries plan_tier=free must still be refused bulk access,
    not silently granted it just because it's otherwise valid/active.
    """
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row(plan_tier="free"))

    client = TestClient(app)
    response = client.post(
        "/v1/domains/bulk", json={"domains": ["example.com"]}, headers={"X-API-Key": "ai_live_free-tier"}
    )

    assert response.status_code == 403


@mock_aws
def test_bulk_over_the_domain_cap_is_a_clean_422_not_500(monkeypatch):
    # A valid, active, paid-tier key (and a working rate-limit table)
    # -- otherwise an earlier dependency's own error (403 forbidden, or
    # a real-AWS-call crash) would mask whichever error this test wants
    # to isolate: the cap-validation behavior specifically.
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row())

    client = TestClient(app)
    over_cap_domains = [f"d{i}.example.com" for i in range(BULK_MAX_DOMAINS + 1)]

    response = client.post(
        "/v1/domains/bulk", json={"domains": over_cap_domains}, headers={"X-API-Key": "ai_live_whatever"}
    )

    assert response.status_code == 422


@mock_aws
def test_bulk_with_a_valid_paid_key_returns_mixed_found_and_not_found(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row())
    monkeypatch.setattr(
        routes,
        "fetch_domains_bulk",
        lambda _conn, domains: {"example.com": {"domain": "example.com", "agent_json_present": True}},
    )

    client = TestClient(app)
    response = client.post(
        "/v1/domains/bulk",
        json={"domains": ["example.com", "never-crawled.example"]},
        headers={"X-API-Key": "ai_live_paid"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    results_by_domain = {r["domain"]: r for r in body["results"]}
    assert results_by_domain["example.com"]["found"] is True
    assert results_by_domain["example.com"]["record"]["agent_json_present"] is True
    assert results_by_domain["never-crawled.example"]["found"] is False
    assert results_by_domain["never-crawled.example"]["record"] is None


@mock_aws
def test_bulk_licensing_tier_key_is_also_allowed(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row(plan_tier="licensing"))
    monkeypatch.setattr(routes, "fetch_domains_bulk", lambda _conn, _domains: {})

    client = TestClient(app)
    response = client.post(
        "/v1/domains/bulk", json={"domains": ["example.com"]}, headers={"X-API-Key": "ai_live_licensing"}
    )

    assert response.status_code == 200


@mock_aws
def test_bulk_is_rate_limited_at_the_keys_own_rate_limit(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    # A tight rate_limit (2) on this key -- deliberately far below the
    # free-tier default -- to prove the bulk endpoint enforces the
    # KEY's own limit, not the free-tier flat rate.
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row(rate_limit=2))
    monkeypatch.setattr(routes, "fetch_domains_bulk", lambda _conn, _domains: {})

    client = TestClient(app)
    headers = {"X-API-Key": "ai_live_tight-limit"}
    assert client.post("/v1/domains/bulk", json={"domains": ["a.com"]}, headers=headers).status_code == 200
    assert client.post("/v1/domains/bulk", json={"domains": ["a.com"]}, headers=headers).status_code == 200

    third = client.post("/v1/domains/bulk", json={"domains": ["a.com"]}, headers=headers)
    assert third.status_code == 429
    assert third.json()["error"] == "rate_limit_exceeded"
