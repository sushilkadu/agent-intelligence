"""`GET /v1/billing/session/{id}` (the no-email key-retrieval MVP
substitute) and `POST /v1/billing/portal`.

DB access is faked here (mirrors every other route-level test file in
this suite); `create_portal_session` (a real Stripe SDK call this
environment can't make -- see billing/stripe_client.py's module
docstring) is monkeypatched.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from billing import routes
from billing.stripe_client import PortalSession


class _DummyConn:
    def close(self) -> None:
        pass


def _key_row(
    key_id="key-1",
    plan_tier="self_serve",
    rate_limit=600,
    pending_secret=None,
    stripe_customer_id="cus_1",
):
    return {
        "key_id": key_id,
        "plan_tier": plan_tier,
        "rate_limit": rate_limit,
        "pending_secret": pending_secret,
        "stripe_customer_id": stripe_customer_id,
    }


def test_session_retrieval_returns_the_key_exactly_once(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(
        routes,
        "find_api_key_by_checkout_session_id",
        lambda _conn, _session_id: _key_row(pending_secret="ai_live_freshly_issued"),
    )
    cleared = []
    monkeypatch.setattr(routes, "clear_pending_secret", lambda _conn, key_id: cleared.append(key_id))

    client = TestClient(app)
    response = client.get("/v1/billing/session/cs_test_1")

    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] == "ai_live_freshly_issued"
    assert body["already_retrieved"] is False
    assert body["plan_tier"] == "self_serve"
    assert cleared == ["key-1"]  # the secret was cleared immediately after being handed out


def test_session_retrieval_a_second_time_reports_already_retrieved(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(
        routes,
        "find_api_key_by_checkout_session_id",
        lambda _conn, _session_id: _key_row(pending_secret=None),
    )
    cleared = []
    monkeypatch.setattr(routes, "clear_pending_secret", lambda _conn, key_id: cleared.append(key_id))

    client = TestClient(app)
    response = client.get("/v1/billing/session/cs_test_1")

    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] is None
    assert body["already_retrieved"] is True
    assert cleared == []  # nothing to clear -- already cleared on the first retrieval


def test_session_retrieval_for_an_unknown_session_is_a_clean_404(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(routes, "find_api_key_by_checkout_session_id", lambda _conn, _session_id: None)

    client = TestClient(app)
    response = client.get("/v1/billing/session/cs_never_existed")

    assert response.status_code == 404
    assert response.json()["error"] == "session_not_found"


def test_portal_by_email_redirects_to_a_real_looking_portal_url(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(routes, "find_latest_active_api_key_by_email", lambda _conn, _email: _key_row())
    monkeypatch.setattr(
        routes,
        "create_portal_session",
        lambda **kwargs: PortalSession(url="https://billing.stripe.com/session/test_portal"),
    )

    client = TestClient(app)
    response = client.post("/v1/billing/portal", json={"email": "buyer@example.com"})

    assert response.status_code == 200
    assert response.json() == {"portal_url": "https://billing.stripe.com/session/test_portal"}


def test_portal_by_api_key_looks_up_by_hash(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    seen = {}

    def _fake_find_by_hash(_conn, key_hash):
        seen["key_hash"] = key_hash
        return _key_row()

    monkeypatch.setattr(routes, "find_api_key_by_hash", _fake_find_by_hash)
    monkeypatch.setattr(
        routes, "create_portal_session", lambda **kwargs: PortalSession(url="https://billing.stripe.com/x")
    )

    client = TestClient(app)
    response = client.post("/v1/billing/portal", json={"api_key": "ai_live_realkey"})

    assert response.status_code == 200
    # The raw key must never be what's looked up by -- only its hash.
    assert seen["key_hash"] != "ai_live_realkey"


def test_portal_requires_exactly_one_of_email_or_api_key():
    client = TestClient(app)

    both = client.post("/v1/billing/portal", json={"email": "a@example.com", "api_key": "ai_live_x"})
    assert both.status_code == 422

    neither = client.post("/v1/billing/portal", json={})
    assert neither.status_code == 422


def test_portal_for_an_unknown_customer_is_a_clean_404(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(routes, "find_latest_active_api_key_by_email", lambda _conn, _email: None)

    client = TestClient(app)
    response = client.post("/v1/billing/portal", json={"email": "nobody@example.com"})

    assert response.status_code == 404
    assert response.json()["error"] == "customer_not_found"
