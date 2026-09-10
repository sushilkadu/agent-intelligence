"""Configuration constants for billing-service.

Mirrors api-service's/parser-service's `config.py` in style: centralize
env var names in one place, default to values that make local
dev/testing work without extra setup.
"""

from __future__ import annotations

import os

from shared_utils import (
    API_KEY_HEADER,
)

# --- AWS resource configuration ------------------------------------------------

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


def boto3_client_kwargs() -> dict:
    """Common kwargs for every boto3 client this service creates."""
    kwargs: dict = {"region_name": AWS_REGION}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs


# --- Database configuration (read + write) --------------------------------
#
# Unlike api-service (read-only) or crawler-service, billing-service
# WRITES to `api_keys` -- it's the only service that issues keys and
# manages their Stripe-driven lifecycle. Same DB_HOST/PORT/NAME/USER
# convention as every other service, same Secrets-Manager-vs-plaintext
# password resolution as parser-service's `parser/config.py` (see
# `billing/db.py`).

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "agent_intel")
DB_USER = os.environ.get("DB_USER", "agent_intel")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "agent_intel")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")

# --- Stripe -----------------------------------------------------------------
#
# Same Secrets-Manager-vs-plaintext resolution pattern as every
# service's DB password (see `billing/db.py`'s `_resolve_password`):
# STRIPE_SECRET_KEY_ARN / STRIPE_WEBHOOK_SECRET_ARN name Secrets
# Manager secrets (populated by Terraform's `secrets` module in real
# deployments -- see infra/terraform/envs/dev/main.tf's billing-service
# section; billing-service's Lambda IAM role reads ONLY these two
# secrets, nothing broader). The plain STRIPE_SECRET_KEY/
# STRIPE_WEBHOOK_SECRET env vars are read directly for local dev, where
# nothing populates a real Secrets Manager secret. Actual resolution
# (which one wins) happens in `billing/stripe_client.py`'s
# `_resolve_secret`, mirroring `_resolve_password`'s "ARN wins if set."
#
# This environment has neither a real Stripe account nor network
# access to api.stripe.com (verified early, see the Phase 4 report), so
# every real Stripe SDK call in this codebase is exercised through
# `billing/stripe_client.py`'s seam and monkeypatched in tests. The
# webhook SIGNATURE VERIFICATION path is the one exception: it's real,
# self-signed HMAC-SHA256 crypto against a fake secret, genuinely
# testable offline (see tests/test_webhook_signature.py).
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_SECRET_KEY_ARN = os.environ.get("STRIPE_SECRET_KEY_ARN", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_WEBHOOK_SECRET_ARN = os.environ.get("STRIPE_WEBHOOK_SECRET_ARN", "")

# The Stripe Price id for the `self_serve` plan's recurring
# subscription. `licensing` has no price here -- see
# `billing/routes.py`'s checkout endpoint docstring on why licensing
# plans are rejected/out-of-band, not priced through Stripe Checkout.
STRIPE_SELF_SERVE_PRICE_ID = os.environ.get("STRIPE_SELF_SERVE_PRICE_ID", "price_self_serve_placeholder")

# Where Stripe Checkout/the customer portal send the browser back to.
# Points at the frontend dashboard (see apps/frontend/app/dashboard).
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000")
CHECKOUT_SUCCESS_URL = os.environ.get(
    "CHECKOUT_SUCCESS_URL", f"{FRONTEND_BASE_URL}/dashboard/success?session_id={{CHECKOUT_SESSION_ID}}"
)
CHECKOUT_CANCEL_URL = os.environ.get("CHECKOUT_CANCEL_URL", f"{FRONTEND_BASE_URL}/dashboard")
PORTAL_RETURN_URL = os.environ.get("PORTAL_RETURN_URL", f"{FRONTEND_BASE_URL}/dashboard")

# --- Self-serve plan defaults ------------------------------------------------
#
# The rate limit a freshly-issued self_serve key gets. Same
# "reasonable, easily-overridable default" judgment call as api-service's
# free-tier RATE_LIMIT_PER_MINUTE -- comfortably above the free tier
# (60/min) to be worth paying for, without picking a number the build
# plan specifies (it doesn't).
SELF_SERVE_RATE_LIMIT = int(os.environ.get("SELF_SERVE_RATE_LIMIT", "600"))

# --- CORS --------------------------------------------------------------
#
# Unlike api-service's public GET lookup routes (permissive `*` is
# defensible there -- see api-service/app.py's Phase 4 comment on why
# bearer-token auth doesn't carry CORS's usual ambient-credential
# risk), billing-service's endpoints take an email address in the
# request body and exist specifically to be called from the
# Agent Intelligence dashboard -- there's no product reason for an
# arbitrary third-party page to POST checkout/portal requests against
# it. Scoped to the known frontend origin(s) instead of "*".
# Comma-separated so multiple environments (local dev + deployed
# Amplify domain) can be configured without code changes.
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

__all__ = [
    "API_KEY_HEADER",
    "AWS_REGION",
    "CHECKOUT_CANCEL_URL",
    "CHECKOUT_SUCCESS_URL",
    "CORS_ALLOWED_ORIGINS",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_SECRET_ARN",
    "DB_USER",
    "FRONTEND_BASE_URL",
    "PORTAL_RETURN_URL",
    "SELF_SERVE_RATE_LIMIT",
    "STRIPE_SECRET_KEY",
    "STRIPE_SECRET_KEY_ARN",
    "STRIPE_SELF_SERVE_PRICE_ID",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_WEBHOOK_SECRET_ARN",
    "boto3_client_kwargs",
]
