"""The one seam where billing-service talks to the real Stripe SDK.

--- What's real vs. what's mocked (read this before touching tests) ------

This environment has no real Stripe test-mode secret key and no
network access to `api.stripe.com` (verified early and cheaply, per
the Phase 4 build plan's own instruction to check before assuming). So:

  * `construct_webhook_event` -- REAL. `stripe.Webhook.construct_event`
    is pure local cryptography (HMAC-SHA256 over
    `{timestamp}.{payload}` against a shared secret, no network call
    at all -- this is exactly how Stripe's own docs say to test webhook
    handlers without a live account). Nothing about this function is
    mocked anywhere; `tests/test_webhook_signature.py` self-signs a
    synthetic event with a fake secret this module never sees hardcoded
    and proves both acceptance (correct signature) and rejection
    (tampered payload / wrong secret) for real.

  * `create_checkout_session` / `create_portal_session` -- NOT
    exercised for real anywhere in this codebase. Both call
    `stripe.checkout.Session.create` / `stripe.billing_portal.Session.create`,
    which genuinely need a live (or network-mocked) Stripe backend.
    Every test that reaches these monkeypatches the FUNCTION ITSELF
    (`billing.stripe_client.create_checkout_session` /
    `.create_portal_session`) rather than the underlying `stripe`
    package -- so no test in this repo proves the real Stripe HTTP
    round-trip works, only that billing-service's own logic calls
    these two functions with the right arguments and does the right
    thing with whatever they return. That's a real gap, flagged in the
    Phase 4 report: these two paths need a real (or `responses`
    /`respx`-mocked-HTTP) round-trip test the moment a real Stripe
    account is wired up.

Keeping both kinds of calls behind this one small module (rather than
scattering `stripe.foo.Bar.create(...)` calls through `billing/routes.py`)
is what makes that monkeypatch seam possible without reaching into the
third-party `stripe` package's internals from test code.
"""

from __future__ import annotations

from dataclasses import dataclass

import boto3
import stripe

from .config import (
    STRIPE_SECRET_KEY,
    STRIPE_SECRET_KEY_ARN,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_WEBHOOK_SECRET_ARN,
    boto3_client_kwargs,
)


def _resolve_secret(plaintext_value: str, secret_arn: str) -> str:
    """Same Secrets-Manager-vs-plaintext resolution as every service's
    DB password (see `billing/db.py`'s `_resolve_password`): if a
    Secrets Manager ARN is configured (real deployments -- see
    `billing/config.py`), it wins; otherwise the plain env var value is
    used as-is (local dev). Unlike the DB password secret (a
    `{"username": ..., "password": ...}` JSON payload AWS itself
    manages), these are plain single-string secrets this module's own
    Terraform `secrets` module entries store, so `SecretString` is read
    directly, no JSON unwrapping.
    """
    if secret_arn:
        client = boto3.client("secretsmanager", **boto3_client_kwargs())
        return client.get_secret_value(SecretId=secret_arn)["SecretString"]
    return plaintext_value


def _resolve_stripe_secret_key() -> str:
    return _resolve_secret(STRIPE_SECRET_KEY, STRIPE_SECRET_KEY_ARN)


def _resolve_stripe_webhook_secret() -> str:
    return _resolve_secret(STRIPE_WEBHOOK_SECRET, STRIPE_WEBHOOK_SECRET_ARN)


@dataclass(frozen=True)
class CheckoutSession:
    """The subset of a real `stripe.checkout.Session` this service
    actually uses -- narrowed to a small local type so callers (and
    tests) don't have to construct/monkeypatch the real, much larger
    Stripe SDK object.
    """

    id: str
    url: str


@dataclass(frozen=True)
class PortalSession:
    url: str


def create_checkout_session(*, email: str, price_id: str, success_url: str, cancel_url: str) -> CheckoutSession:
    """Create a Stripe Checkout Session in subscription mode for the
    self-serve price. NOT exercised for real in this codebase's tests
    -- see module docstring.
    """
    session = stripe.checkout.Session.create(
        api_key=_resolve_stripe_secret_key(),
        mode="subscription",
        customer_email=email,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return CheckoutSession(id=session["id"], url=session["url"])


def create_portal_session(*, customer_id: str, return_url: str) -> PortalSession:
    """Create a Stripe customer billing-portal session. NOT exercised
    for real in this codebase's tests -- see module docstring.
    """
    session = stripe.billing_portal.Session.create(
        api_key=_resolve_stripe_secret_key(),
        customer=customer_id,
        return_url=return_url,
    )
    return PortalSession(url=session["url"])


def construct_webhook_event(*, payload: bytes, sig_header: str, webhook_secret: str = "") -> stripe.Event:
    """Verify + parse an incoming Stripe webhook payload. REAL --
    pure local HMAC-SHA256 verification, no network call. Raises
    `stripe.error.SignatureVerificationError` for a tampered payload or
    a signature that doesn't match `webhook_secret`; `billing/routes.py`
    turns that into a clean 400.
    """
    return stripe.Webhook.construct_event(
        payload,
        sig_header,
        webhook_secret or _resolve_stripe_webhook_secret(),
    )


__all__ = ["CheckoutSession", "PortalSession", "construct_webhook_event", "create_checkout_session", "create_portal_session"]
