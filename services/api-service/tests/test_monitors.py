"""`POST /v1/monitors` / `DELETE /v1/monitors/{id}`:

  * registration requires an active self_serve/licensing key (missing/
    invalid/free-tier -> clean 403, mirrors `tests/test_bulk.py`'s
    tier-gating tests for `POST /v1/domains/bulk`);
  * `webhook_url` is validated for SSRF safety BEFORE any row is
    created (400, never a 500, never a persisted unsafe URL);
  * deletion only succeeds for the owning key -- a monitor that doesn't
    exist and a monitor owned by someone else both come back as the
    SAME 404 (see api/routes.py's `delete_monitor_route` docstring for
    why that's deliberate, not an oversight).

DB access is faked (mirrors tests/test_bulk.py/test_domains.py); only
DynamoDB (rate limiting) is real, via moto.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from api import auth, routes
from app import app

RATE_LIMIT_TABLE = "agent-intel-monitors-test"
NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


class _DummyConn:
    def close(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
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


def _base_setup(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())


# --- registration: tier gating ----------------------------------------------


def test_create_monitor_with_no_api_key_is_a_clean_403():
    client = TestClient(app)

    response = client.post("/v1/monitors", json={"domain": "example.com", "webhook_url": "https://hooks.example.com/x"})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@mock_aws
def test_create_monitor_with_a_free_tier_key_is_a_clean_403(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row(plan_tier="free"))

    client = TestClient(app)
    response = client.post(
        "/v1/monitors",
        json={"domain": "example.com", "webhook_url": "https://hooks.example.com/x"},
        headers={"X-API-Key": "ai_live_free"},
    )

    assert response.status_code == 403


# --- registration: SSRF validation -------------------------------------------


@mock_aws
def test_create_monitor_rejects_non_https_webhook_url(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row())

    client = TestClient(app)
    response = client.post(
        "/v1/monitors",
        json={"domain": "example.com", "webhook_url": "http://hooks.example.com/x"},
        headers={"X-API-Key": "ai_live_paid"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_webhook_url"


@mock_aws
def test_create_monitor_rejects_private_ip_webhook_url(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row())

    client = TestClient(app)
    response = client.post(
        "/v1/monitors",
        json={"domain": "example.com", "webhook_url": "https://10.0.0.5/x"},
        headers={"X-API-Key": "ai_live_paid"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_webhook_url"


@mock_aws
def test_create_monitor_rejects_cloud_metadata_webhook_url(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row())

    client = TestClient(app)
    response = client.post(
        "/v1/monitors",
        json={"domain": "example.com", "webhook_url": "https://169.254.169.254/latest/meta-data/"},
        headers={"X-API-Key": "ai_live_paid"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_webhook_url"


@mock_aws
def test_create_monitor_accepts_a_safe_https_url_and_persists_owner(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row(key_id="key-owner"))

    captured = {}

    def _fake_insert(_conn, *, monitor_id, domain, webhook_url, owner_key_id, created_at):
        captured["monitor_id"] = monitor_id
        captured["domain"] = domain
        captured["webhook_url"] = webhook_url
        captured["owner_key_id"] = owner_key_id
        return {
            "monitor_id": monitor_id,
            "domain": domain,
            "webhook_url": webhook_url,
            "owner_key_id": owner_key_id,
            "created_at": created_at,
        }

    monkeypatch.setattr(routes, "insert_monitor", _fake_insert)

    # A real, public-resolving hostname is needed since this exercises
    # the actual `socket.getaddrinfo` resolver (no injected fake here) --
    # localhost's loopback address is deliberately unsafe, so a
    # resolver stand-in is used instead of relying on real DNS/network
    # access in a unit test.
    import shared_utils.webhook_safety as safety_module

    monkeypatch.setattr(safety_module.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 0))])

    client = TestClient(app)
    response = client.post(
        "/v1/monitors",
        json={"domain": "example.com", "webhook_url": "https://hooks.example.com/callback"},
        headers={"X-API-Key": "ai_live_paid"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["domain"] == "example.com"
    assert body["webhook_url"] == "https://hooks.example.com/callback"
    assert body["owner_key_id"] == "key-owner"
    assert captured["owner_key_id"] == "key-owner"
    # A real UUID was generated for the new monitor.
    uuid.UUID(body["monitor_id"])


@mock_aws
def test_create_monitor_for_unknown_domain_is_a_clean_400_not_500(monkeypatch):
    """An FK violation (monitors.domain -> domains.domain) surfaces as a
    clean 400, not an unhandled 500 -- the caller named a domain this
    system has never crawled.
    """
    import psycopg2

    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row())

    def _raise_integrity_error(_conn, **_kwargs):
        raise psycopg2.IntegrityError("insert or update on table monitors violates foreign key constraint")

    monkeypatch.setattr(routes, "insert_monitor", _raise_integrity_error)

    import shared_utils.webhook_safety as safety_module

    monkeypatch.setattr(safety_module.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 0))])

    client = TestClient(app)
    response = client.post(
        "/v1/monitors",
        json={"domain": "never-crawled.example", "webhook_url": "https://hooks.example.com/callback"},
        headers={"X-API-Key": "ai_live_paid"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unknown_domain"


# --- deletion: ownership, no-existence-leak ----------------------------------


@mock_aws
def test_delete_monitor_with_no_api_key_is_a_clean_401(monkeypatch):
    _base_setup(monkeypatch)

    client = TestClient(app)
    response = client.delete(f"/v1/monitors/{uuid.uuid4()}")

    assert response.status_code == 401


@mock_aws
def test_delete_nonexistent_monitor_is_404(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row(key_id="key-1"))
    monkeypatch.setattr(routes, "fetch_monitor", lambda _conn, _id: None)

    client = TestClient(app)
    response = client.delete(f"/v1/monitors/{uuid.uuid4()}", headers={"X-API-Key": "ai_live_key1"})

    assert response.status_code == 404
    assert response.json()["error"] == "monitor_not_found"


@mock_aws
def test_delete_someone_elses_monitor_is_the_same_404_not_403(monkeypatch):
    """Ownership mismatch and nonexistence must look identical to the
    caller -- otherwise a 403-vs-404 split would let a caller enumerate
    other customers' monitor ids.
    """
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row(key_id="key-attacker"))
    other_monitor_id = uuid.uuid4()
    monkeypatch.setattr(
        routes,
        "fetch_monitor",
        lambda _conn, _id: {"monitor_id": other_monitor_id, "domain": "example.com", "owner_key_id": "key-victim"},
    )

    client = TestClient(app)
    response = client.delete(f"/v1/monitors/{other_monitor_id}", headers={"X-API-Key": "ai_live_attacker"})

    assert response.status_code == 404
    assert response.json()["error"] == "monitor_not_found"


@mock_aws
def test_delete_own_monitor_succeeds(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row(key_id="key-owner"))
    monitor_id = uuid.uuid4()
    monkeypatch.setattr(
        routes,
        "fetch_monitor",
        lambda _conn, _id: {"monitor_id": monitor_id, "domain": "example.com", "owner_key_id": "key-owner"},
    )
    deleted = {}
    monkeypatch.setattr(routes, "delete_monitor", lambda _conn, _id: deleted.setdefault("id", _id))

    client = TestClient(app)
    response = client.delete(f"/v1/monitors/{monitor_id}", headers={"X-API-Key": "ai_live_owner"})

    assert response.status_code == 204
    assert deleted["id"] == monitor_id


@mock_aws
def test_delete_monitor_with_a_malformed_id_is_404_not_500(monkeypatch):
    _base_setup(monkeypatch)
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row())

    client = TestClient(app)
    response = client.delete("/v1/monitors/not-a-uuid", headers={"X-API-Key": "ai_live_paid"})

    assert response.status_code == 404
