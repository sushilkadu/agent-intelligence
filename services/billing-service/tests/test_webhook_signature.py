"""REAL cryptographic verification of Stripe webhook signatures --
no mocking, no monkeypatching, no network access.

Stripe's webhook signature scheme (documented at
https://docs.stripe.com/webhooks#verify-manually) is: take
`signed_payload = f"{timestamp}.{payload}"`, compute
`hmac.new(webhook_secret, signed_payload, sha256).hexdigest()`, and send
it as the `Stripe-Signature` header in the form
`t=<timestamp>,v1=<hex digest>`. This test file implements that exact
scheme itself (via `hmac`/`hashlib`, not by calling into
`billing.stripe_client` or any Stripe SDK helper) to sign a synthetic
event payload with a fake secret THIS TEST GENERATES, then proves
`billing.stripe_client.construct_webhook_event` (which calls the real
`stripe.Webhook.construct_event`) both ACCEPTS a correctly-signed
payload and REJECTS a tampered one / one signed with the wrong secret.

This is exactly how Stripe's own docs recommend testing webhook
handlers without a live account -- see
https://docs.stripe.com/webhooks#test-webhook -- and is the one piece
of this service's Stripe integration that is fully real, not mocked
(contrast with Checkout/portal session creation -- see
`billing/stripe_client.py`'s module docstring).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
import stripe

from billing.stripe_client import construct_webhook_event

FAKE_WEBHOOK_SECRET = "whsec_this_is_a_fake_secret_generated_for_testing_only"


def _sign(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Implements Stripe's documented signing scheme from scratch --
    deliberately not delegating to any Stripe SDK helper, so this is a
    genuinely independent re-implementation of the scheme, not a
    tautological "does the library agree with itself" check.
    """
    timestamp = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    digest = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _sample_event_payload(event_type: str = "checkout.session.completed") -> bytes:
    return json.dumps(
        {
            "id": "evt_test_1",
            "object": "event",
            "type": event_type,
            "data": {"object": {"id": "cs_test_1", "object": "checkout.session"}},
        }
    ).encode("utf-8")


def test_construct_event_accepts_a_correctly_signed_payload():
    payload = _sample_event_payload()
    sig_header = _sign(payload, FAKE_WEBHOOK_SECRET)

    event = construct_webhook_event(payload=payload, sig_header=sig_header, webhook_secret=FAKE_WEBHOOK_SECRET)

    assert event["type"] == "checkout.session.completed"
    assert event["id"] == "evt_test_1"


def test_construct_event_rejects_a_tampered_payload():
    payload = _sample_event_payload()
    sig_header = _sign(payload, FAKE_WEBHOOK_SECRET)

    # Sign the original payload, then send a DIFFERENT payload with
    # that same (now-invalid) signature header -- simulates an
    # attacker modifying the body in transit.
    tampered_payload = _sample_event_payload(event_type="customer.subscription.deleted")

    with pytest.raises(stripe.error.SignatureVerificationError):
        construct_webhook_event(payload=tampered_payload, sig_header=sig_header, webhook_secret=FAKE_WEBHOOK_SECRET)


def test_construct_event_rejects_a_signature_from_the_wrong_secret():
    payload = _sample_event_payload()
    sig_header = _sign(payload, "whsec_a_completely_different_secret")

    with pytest.raises(stripe.error.SignatureVerificationError):
        construct_webhook_event(payload=payload, sig_header=sig_header, webhook_secret=FAKE_WEBHOOK_SECRET)


def test_construct_event_rejects_a_missing_signature_header():
    payload = _sample_event_payload()

    with pytest.raises(stripe.error.SignatureVerificationError):
        construct_webhook_event(payload=payload, sig_header="", webhook_secret=FAKE_WEBHOOK_SECRET)


def test_construct_event_rejects_an_expired_timestamp():
    """Stripe's own verification also enforces a tolerance window
    (default 5 minutes) on the signature's timestamp, to reject
    replayed old requests -- proving that's active too, not just the
    HMAC check itself.
    """
    payload = _sample_event_payload()
    ancient_timestamp = int(time.time()) - 10_000
    sig_header = _sign(payload, FAKE_WEBHOOK_SECRET, timestamp=ancient_timestamp)

    with pytest.raises(stripe.error.SignatureVerificationError):
        construct_webhook_event(payload=payload, sig_header=sig_header, webhook_secret=FAKE_WEBHOOK_SECRET)
