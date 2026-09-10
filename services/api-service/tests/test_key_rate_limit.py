"""Phase 4: the two original public GET routes' *optional* API-key
auth path (`api/auth.py` + `enforce_rate_limit`'s header-present
branch), and the new `GET /v1/keys/me` usage endpoint.

`tests/test_ratelimit.py` already proves the no-header free-tier path
is unchanged; this file proves the header-present path: a
present-but-invalid key is a clean 401 (never a 500, never a silent
fallback to free-tier), and a valid key is rate-limited at ITS OWN
`rate_limit` via a DynamoDB item distinct from any IP-keyed window.
"""

from __future__ import annotations

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from api import auth, routes
from app import app

RATE_LIMIT_TABLE = "agent-intel-key-rate-limit-test"
BUCKET = "agent-intel-raw-crawl-key-rate-limit-test"


class _DummyConn:
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


@mock_aws
def test_an_invalid_api_key_on_the_lookup_route_is_a_clean_401(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: None)
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)

    client = TestClient(app)
    response = client.get("/v1/domains/example.com", headers={"X-API-Key": "ai_live_not-a-real-key"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_api_key"


@mock_aws
def test_free_tier_lookup_with_no_header_is_unaffected_by_phase_4(monkeypatch):
    """Regression check: a caller that never sends X-API-Key must get
    exactly Phase 3's original behavior -- no auth check, no DB touch
    from the auth path, plain IP-keyed rate limiting.
    """
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, domain: {"domain": domain} if domain == "example.com" else None)

    client = TestClient(app)
    response = client.get("/v1/domains/example.com")

    assert response.status_code == 200
    assert response.json()["domain"] == "example.com"


@mock_aws
def test_valid_key_is_rate_limited_at_its_own_rate_not_the_free_tier_rate(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    monkeypatch.setattr(routes, "RAW_DATA_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(routes, "RATE_LIMIT_PER_MINUTE", 60)  # generous free-tier default, deliberately unused here
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    # A tight rate_limit (2) on the key -- far below the free-tier
    # default configured above -- proves this request is limited by
    # the KEY's own rate, not the flat free-tier rate.
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row(rate_limit=2))

    client = TestClient(app)
    headers = {"X-API-Key": "ai_live_tight-limit"}
    assert client.get("/v1/domains/example.com/history", headers=headers).status_code == 200
    assert client.get("/v1/domains/example.com/history", headers=headers).status_code == 200

    third = client.get("/v1/domains/example.com/history", headers=headers)
    assert third.status_code == 429
    body = third.json()
    assert body["error"] == "rate_limit_exceeded"
    assert "self_serve" in body["message"]


@mock_aws
def test_key_and_ip_windows_are_independent(monkeypatch):
    """An authenticated key's counter and the free-tier IP counter must
    never share state -- hitting the key-limited path repeatedly must
    not exhaust an unauthenticated caller's separate free-tier budget
    (and vice versa).
    """
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    monkeypatch.setattr(routes, "RAW_DATA_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(routes, "RATE_LIMIT_PER_MINUTE", 60)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row(rate_limit=1))

    client = TestClient(app)
    headers = {"X-API-Key": "ai_live_solo"}
    assert client.get("/v1/domains/example.com/history", headers=headers).status_code == 200
    assert client.get("/v1/domains/example.com/history", headers=headers).status_code == 429

    # The same-process, unauthenticated (no header) request must still
    # be well within ITS OWN separate free-tier budget.
    assert client.get("/v1/domains/example.com/history").status_code == 200


@mock_aws
def test_keys_me_reports_plan_tier_rate_limit_and_current_window_count(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(
        auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _key_row(key_id="key-42", rate_limit=500)
    )

    client = TestClient(app)
    headers = {"X-API-Key": "ai_live_usage-check"}

    first = client.get("/v1/keys/me", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert body["key_id"] == "key-42"
    assert body["plan_tier"] == "self_serve"
    assert body["rate_limit"] == 500
    assert body["current_window_count"] == 1  # this call itself counted against the window

    second = client.get("/v1/keys/me", headers=headers)
    assert second.json()["current_window_count"] == 2


@mock_aws
def test_keys_me_with_no_header_is_a_clean_401(monkeypatch):
    # `GET /v1/keys/me` also carries the general `enforce_rate_limit`
    # dependency (see api/routes.py) -- on the no-header path that
    # still does a real (moto-mocked) DynamoDB free-tier IP check
    # before `require_any_api_key` gets a chance to reject the missing
    # key, so the table needs to exist regardless of which dependency
    # "wins" the race.
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    client = TestClient(app)
    response = client.get("/v1/keys/me")

    assert response.status_code == 401
    assert response.json()["error"] == "missing_api_key"
