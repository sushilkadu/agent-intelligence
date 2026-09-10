"""billing-service's v1 routes: Stripe Checkout, the Stripe webhook,
one-time key retrieval, and the customer billing portal.

See `billing/stripe_client.py`'s module docstring for exactly which
Stripe calls are real (webhook signature verification) vs. mocked in
tests (Checkout/portal session creation) in this environment.
"""

from __future__ import annotations

import logging
import uuid

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from shared_utils import generate_api_key_secret, hash_api_key_secret

from .config import (
    CHECKOUT_CANCEL_URL,
    CHECKOUT_SUCCESS_URL,
    PORTAL_RETURN_URL,
    SELF_SERVE_RATE_LIMIT,
    STRIPE_SELF_SERVE_PRICE_ID,
)
from .db import (
    clear_pending_secret,
    create_api_key_for_checkout_session,
    find_api_key_by_checkout_session_id,
    find_api_key_by_hash,
    find_latest_active_api_key_by_email,
    get_connection,
    update_subscription_status,
)
from .schemas import (
    CheckoutRequest,
    CheckoutResponse,
    PortalRequest,
    PortalResponse,
    SessionKeyResponse,
)
from .stripe_client import (
    construct_webhook_event,
    create_checkout_session,
    create_portal_session,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Subscription statuses that count as "this key should stay active."
# Everything else (past_due, canceled, unpaid, incomplete,
# incomplete_expired, paused) deactivates the key. This is a
# deliberately simple MVP mapping -- a real product would likely want
# a grace period / dunning-email flow for `past_due` before yanking
# access; flagged in the Phase 4 report as worth revisiting once a
# real Stripe account (and real customers who occasionally have a card
# decline) is in the picture.
_ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})


def _stripe_call_failed(exc: stripe.error.StripeError) -> HTTPException:
    """A real `stripe.checkout.Session.create`/`stripe.billing_portal.Session.create`
    call can fail for reasons entirely outside this service's control --
    bad/missing credentials (verified locally: this environment has no
    real Stripe secret key, so this path was actually exercised and
    confirmed to raise `stripe.error.AuthenticationError` for real, not
    just a network-unreachable/timeout error -- see the Phase 4
    report), a misconfigured Price id, Stripe being down, etc. Without
    this, FastAPI's default handling would surface those as a bare 500
    with no JSON body -- a clean 502 (this service correctly called an
    upstream that failed) is a more honest/debuggable shape, and is
    never the 400/403/404 shapes this codebase uses for THIS service's
    own validation failures.
    """
    return HTTPException(
        status_code=502,
        detail={"error": "stripe_error", "message": f"Stripe request failed: {exc}"},
    )


def _get_db_connection():
    """FastAPI dependency: one Postgres connection per request, closed
    when the request finishes (same pattern as api-service's
    `api/routes.py`).
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


@router.post("/v1/billing/checkout", response_model=CheckoutResponse)
def start_checkout(payload: CheckoutRequest) -> CheckoutResponse:
    """Create a Stripe Checkout Session (subscription mode) for the
    self-serve plan and return its URL for the frontend to redirect to.

    `licensing` is deliberately rejected here, not silently accepted --
    per the original architecture's plan_tier design, `licensing` is a
    manual/off-platform arrangement (a sales conversation, a
    hand-provisioned key), not something Stripe Checkout should ever
    sell self-service. `free` isn't purchasable at all -- it's the
    unauthenticated path, never a Stripe subscription.
    """
    if payload.plan_tier != "self_serve":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_plan_tier",
                "message": (
                    f"'{payload.plan_tier}' cannot be purchased through this endpoint. Only 'self_serve' is "
                    "self-service; 'licensing' plans are provisioned out-of-band -- contact sales."
                ),
            },
        )

    try:
        session = create_checkout_session(
            email=payload.email,
            price_id=STRIPE_SELF_SERVE_PRICE_ID,
            success_url=CHECKOUT_SUCCESS_URL,
            cancel_url=CHECKOUT_CANCEL_URL,
        )
    except stripe.error.StripeError as exc:
        raise _stripe_call_failed(exc) from exc
    return CheckoutResponse(checkout_url=session.url)


def _handle_checkout_completed(conn, session_obj: dict) -> None:
    """`checkout.session.completed`: mint a brand-new API key for this
    session, idempotently (see `create_api_key_for_checkout_session`'s
    docstring for exactly how a redelivered event becomes a no-op
    instead of a second key).
    """
    customer_details = session_obj.get("customer_details") or {}
    email = customer_details.get("email") or session_obj.get("customer_email")
    stripe_customer_id = session_obj.get("customer")
    stripe_checkout_session_id = session_obj.get("id")

    if not (email and stripe_customer_id and stripe_checkout_session_id):
        logger.warning(
            "checkout.session.completed missing email/customer/session id -- ignoring malformed event"
        )
        return

    secret = generate_api_key_secret()
    key_hash = hash_api_key_secret(secret)
    key_id = str(uuid.uuid4())

    inserted = create_api_key_for_checkout_session(
        conn,
        key_id=key_id,
        key_hash=key_hash,
        pending_secret=secret,
        owner_email=email,
        plan_tier="self_serve",
        rate_limit=SELF_SERVE_RATE_LIMIT,
        stripe_customer_id=stripe_customer_id,
        stripe_checkout_session_id=stripe_checkout_session_id,
    )
    if not inserted:
        logger.info(
            "checkout.session.completed for session %s already processed -- redelivery no-op",
            stripe_checkout_session_id,
        )


def _handle_subscription_change(conn, event_type: str, subscription_obj: dict) -> None:
    """`customer.subscription.updated`/`.deleted`: (de)activate the
    `api_keys` row for this Stripe customer, if one exists.
    """
    stripe_customer_id = subscription_obj.get("customer")
    stripe_subscription_id = subscription_obj.get("id")

    if not stripe_customer_id:
        logger.warning("%s event missing customer id -- ignoring malformed event", event_type)
        return

    if event_type == "customer.subscription.deleted":
        active = False
    else:
        active = subscription_obj.get("status") in _ACTIVE_SUBSCRIPTION_STATUSES

    updated = update_subscription_status(
        conn,
        stripe_customer_id=stripe_customer_id,
        active=active,
        stripe_subscription_id=stripe_subscription_id,
    )
    if not updated:
        # Not necessarily a bug -- e.g. a subscription created/managed
        # entirely outside this codebase. Logged, not raised: Stripe
        # retries webhook deliveries that return non-2xx, and there's
        # nothing to retry into existing here.
        logger.warning(
            "%s for stripe_customer_id=%s but no matching api_keys row exists", event_type, stripe_customer_id
        )


@router.post("/v1/billing/webhook")
async def stripe_webhook(request: Request, conn=Depends(_get_db_connection)) -> dict:  # noqa: B008 (standard FastAPI DI idiom)
    """Verify + handle an incoming Stripe webhook event.

    Signature verification (`construct_webhook_event`) is real
    cryptography, not mocked -- see `billing/stripe_client.py`'s module
    docstring and `tests/test_webhook_signature.py`. A bad signature is
    a clean 400, never a 500 and never processed as if it were genuine.
    """
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = construct_webhook_event(payload=payload, sig_header=sig_header)
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_signature", "message": f"Webhook signature verification failed: {exc}"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_payload", "message": f"Malformed webhook payload: {exc}"}
        ) from exc

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(conn, data_object)
    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        _handle_subscription_change(conn, event_type, data_object)
    else:
        logger.info("Ignoring unhandled Stripe event type: %s", event_type)

    return {"received": True}


@router.get("/v1/billing/session/{checkout_session_id}", response_model=SessionKeyResponse)
def get_checkout_session_key(checkout_session_id: str, conn=Depends(_get_db_connection)) -> SessionKeyResponse:  # noqa: B008 (standard FastAPI DI idiom)
    """Retrieve the API key issued for a completed Checkout Session --
    the no-email-service MVP substitute for "we emailed you your key."

    KNOWN GAP (flagged plainly, not silently shipped): this verifies
    the session against OUR OWN idempotent webhook-created record
    (`stripe_checkout_session_id`, unique per the Phase 4 migration),
    not by calling Stripe again -- simpler and fully offline-testable,
    at the cost of trusting our own webhook processing rather than
    Stripe's live session state. The plaintext secret is returned
    exactly ONCE (`already_retrieved` tells the caller which case this
    is) and then cleared from the DB -- see
    `shared_schema.models.ApiKey.pending_secret`'s docstring for why
    this is a real, if narrow, plaintext-at-rest window rather than a
    strict "never persisted" guarantee, and why a real
    email-the-customer flow should replace this before this is a
    product anyone but this build phase relies on.
    """
    row = find_api_key_by_checkout_session_id(conn, checkout_session_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "session_not_found",
                "message": (
                    "This checkout session hasn't been processed yet (or never existed). If you just completed "
                    "checkout, the webhook may not have landed yet -- wait a few seconds and try again."
                ),
            },
        )

    pending_secret = row.get("pending_secret")
    if pending_secret is not None:
        clear_pending_secret(conn, row["key_id"])

    return SessionKeyResponse(
        key_id=row["key_id"],
        plan_tier=row["plan_tier"],
        rate_limit=row["rate_limit"],
        api_key=pending_secret,
        already_retrieved=pending_secret is None,
    )


@router.post("/v1/billing/portal", response_model=PortalResponse)
def start_portal_session(payload: PortalRequest, conn=Depends(_get_db_connection)) -> PortalResponse:  # noqa: B008 (standard FastAPI DI idiom)
    """Create a Stripe customer billing-portal session for subscription
    self-management (cancel, update payment method, view invoices).
    Identifies the customer by email OR API key (`PortalRequest`
    enforces exactly one is given).
    """
    if payload.api_key:
        row = find_api_key_by_hash(conn, hash_api_key_secret(payload.api_key))
    else:
        row = find_latest_active_api_key_by_email(conn, payload.email)  # type: ignore[arg-type]

    if row is None or not row.get("stripe_customer_id"):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "customer_not_found",
                "message": "No billing account was found for that email/API key.",
            },
        )

    try:
        session = create_portal_session(customer_id=row["stripe_customer_id"], return_url=PORTAL_RETURN_URL)
    except stripe.error.StripeError as exc:
        raise _stripe_call_failed(exc) from exc
    return PortalResponse(portal_url=session.url)


__all__ = ["router"]
