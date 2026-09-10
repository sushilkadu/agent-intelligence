"""`POST /v1/billing/checkout`.

`billing.stripe_client.create_checkout_session` is monkeypatched here
-- this environment has no real Stripe test-mode key/network access
(see billing/stripe_client.py's module docstring), so this suite
proves billing-service's OWN logic (which plan tiers are accepted,
what gets passed to Stripe, what the response looks like), not a real
Stripe HTTP round-trip.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from billing import routes
from billing.stripe_client import CheckoutSession

client = TestClient(app)


def test_checkout_rejects_licensing_tier_with_a_clean_400(monkeypatch):
    called = {}
    monkeypatch.setattr(
        routes,
        "create_checkout_session",
        lambda **kwargs: called.update(kwargs) or CheckoutSession(id="cs_should_not_happen", url="unused"),
    )

    response = client.post("/v1/billing/checkout", json={"email": "buyer@example.com", "plan_tier": "licensing"})

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_plan_tier"
    assert called == {}  # Stripe must never be called for a rejected tier


def test_checkout_rejects_free_tier_with_a_clean_400():
    response = client.post("/v1/billing/checkout", json={"email": "buyer@example.com", "plan_tier": "free"})

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_plan_tier"


def test_checkout_creates_a_self_serve_session_and_returns_its_url(monkeypatch):
    captured = {}

    def _fake_create_checkout_session(**kwargs):
        captured.update(kwargs)
        return CheckoutSession(id="cs_test_123", url="https://checkout.stripe.com/pay/cs_test_123")

    monkeypatch.setattr(routes, "create_checkout_session", _fake_create_checkout_session)

    response = client.post("/v1/billing/checkout", json={"email": "buyer@example.com", "plan_tier": "self_serve"})

    assert response.status_code == 200
    assert response.json() == {"checkout_url": "https://checkout.stripe.com/pay/cs_test_123"}
    assert captured["email"] == "buyer@example.com"
    assert "price_" in captured["price_id"]
    assert captured["success_url"]
    assert captured["cancel_url"]
