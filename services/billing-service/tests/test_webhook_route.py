"""`POST /v1/billing/webhook` at the route level.

Signature verification is REAL: every request in this file is signed
with the exact HMAC-SHA256 scheme `tests/test_webhook_signature.py`
proves `construct_webhook_event` implements, using a fake secret this
test configures via `STRIPE_WEBHOOK_SECRET`. DB access is faked (the
`billing.db` functions are monkeypatched directly, mirroring how
api-service's route tests fake `fetch_domain`) so this suite proves the
route's OWN business logic -- which event types do what, idempotency
at the call-argument level -- independent of whether a real Postgres
is reachable. `tests/test_db.py` separately proves the real
INSERT ... ON CONFLICT idempotency against a real table.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import app
from billing import routes, stripe_client

FAKE_WEBHOOK_SECRET = "whsec_route_test_fake_secret"


@pytest.fixture(autouse=True)
def _configure_fake_webhook_secret(monkeypatch):
    """`construct_webhook_event` (see billing/stripe_client.py) falls
    back to the module-level `STRIPE_WEBHOOK_SECRET` config constant
    when no secret is passed explicitly -- exactly what
    `POST /v1/billing/webhook` does. Monkeypatching it here (rather
    than the env var, which is only read once at import time) is what
    lets every test in this file sign with a known fake secret and
    have the route's real verification actually accept it.
    """
    monkeypatch.setattr(stripe_client, "STRIPE_WEBHOOK_SECRET", FAKE_WEBHOOK_SECRET)


class _DummyConn:
    def close(self) -> None:
        pass


def _sign(payload: bytes, secret: str = FAKE_WEBHOOK_SECRET) -> str:
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    digest = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _post_webhook(client: TestClient, event: dict):
    payload = json.dumps(event).encode("utf-8")
    return client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": _sign(payload), "Content-Type": "application/json"},
    )


def _checkout_completed_event(session_id="cs_test_1", customer_id="cus_test_1", email="buyer@example.com") -> dict:
    return {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "customer": customer_id,
                "customer_details": {"email": email},
            }
        },
    }


def _subscription_event(event_type: str, customer_id="cus_test_1", subscription_id="sub_test_1", status=None) -> dict:
    obj = {"id": subscription_id, "customer": customer_id}
    if status is not None:
        obj["status"] = status
    return {"id": "evt_2", "type": event_type, "data": {"object": obj}}


def test_webhook_rejects_an_incorrectly_signed_request_with_400(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())

    client = TestClient(app)
    payload = json.dumps(_checkout_completed_event()).encode("utf-8")
    response = client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": "t=123,v1=not-a-real-signature", "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_signature"


def test_checkout_completed_creates_a_new_api_key(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())

    calls = []
    monkeypatch.setattr(
        routes,
        "create_api_key_for_checkout_session",
        lambda _conn, **kwargs: calls.append(kwargs) or True,
    )

    client = TestClient(app)
    response = _post_webhook(client, _checkout_completed_event(session_id="cs_new", customer_id="cus_new"))

    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert len(calls) == 1
    assert calls[0]["owner_email"] == "buyer@example.com"
    assert calls[0]["plan_tier"] == "self_serve"
    assert calls[0]["stripe_customer_id"] == "cus_new"
    assert calls[0]["stripe_checkout_session_id"] == "cs_new"
    # The plaintext secret handed to the DB layer must actually look
    # like a real generated secret, not e.g. accidentally the key_hash.
    assert calls[0]["pending_secret"].startswith("ai_live_")
    assert calls[0]["key_hash"] != calls[0]["pending_secret"]


def test_checkout_completed_redelivery_does_not_error_when_db_layer_no_ops(monkeypatch):
    """The DB layer's `ON CONFLICT DO NOTHING` (proven for real in
    tests/test_db.py) returning False (already processed) must still
    be a clean 200 -- Stripe treats non-2xx as "please retry," and
    there is nothing to retry into for an already-handled event.
    """
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(routes, "create_api_key_for_checkout_session", lambda _conn, **kwargs: False)

    client = TestClient(app)
    response = _post_webhook(client, _checkout_completed_event())

    assert response.status_code == 200
    assert response.json() == {"received": True}


def test_checkout_completed_with_missing_customer_details_does_not_crash(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    calls = []
    monkeypatch.setattr(
        routes, "create_api_key_for_checkout_session", lambda _conn, **kwargs: calls.append(kwargs) or True
    )

    malformed_event = {"id": "evt_bad", "type": "checkout.session.completed", "data": {"object": {"id": "cs_bad"}}}
    client = TestClient(app)
    response = _post_webhook(client, malformed_event)

    assert response.status_code == 200  # acknowledged, not retried forever
    assert calls == []  # but no key was ever created from incomplete data


def test_subscription_deleted_deactivates_the_matching_key(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    calls = []
    monkeypatch.setattr(
        routes,
        "update_subscription_status",
        lambda _conn, **kwargs: calls.append(kwargs) or True,
    )

    client = TestClient(app)
    response = _post_webhook(client, _subscription_event("customer.subscription.deleted", customer_id="cus_del"))

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["stripe_customer_id"] == "cus_del"
    assert calls[0]["active"] is False


def test_subscription_updated_to_active_status_reactivates_the_key(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    calls = []
    monkeypatch.setattr(
        routes, "update_subscription_status", lambda _conn, **kwargs: calls.append(kwargs) or True
    )

    client = TestClient(app)
    response = _post_webhook(
        client, _subscription_event("customer.subscription.updated", customer_id="cus_upd", status="active")
    )

    assert response.status_code == 200
    assert calls[0]["active"] is True


def test_subscription_updated_to_past_due_deactivates_the_key(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    calls = []
    monkeypatch.setattr(
        routes, "update_subscription_status", lambda _conn, **kwargs: calls.append(kwargs) or True
    )

    client = TestClient(app)
    response = _post_webhook(
        client, _subscription_event("customer.subscription.updated", customer_id="cus_pd", status="past_due")
    )

    assert response.status_code == 200
    assert calls[0]["active"] is False


def test_unrecognized_event_type_is_acknowledged_and_ignored(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())

    client = TestClient(app)
    event = {"id": "evt_ignored", "type": "invoice.paid", "data": {"object": {}}}
    response = _post_webhook(client, event)

    assert response.status_code == 200
    assert response.json() == {"received": True}
