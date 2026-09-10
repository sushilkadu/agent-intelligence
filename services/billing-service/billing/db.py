"""Runtime Postgres access for billing-service: issue+manage `api_keys`
rows.

Deliberately plain psycopg2 + hand-written SQL, mirroring
parser-service's `parser/db.py` in style (connection setup,
Secrets-Manager-vs-plaintext password resolution) -- NOT a copy of it
or an import from it. Each service gets its own small, independent copy
of this pattern so a parser-service change can never break
billing-service at import time (same reasoning parser-service's/
api-service's own `db.py` docstrings give for not sharing this module
across services).

billing-service is the only service that WRITES to `api_keys` --
api-service only ever reads it (see api-service's `api/db.py`'s
`fetch_api_key_by_hash`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3
import psycopg2
import psycopg2.extras

from .config import (
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_SECRET_ARN,
    DB_USER,
    boto3_client_kwargs,
)


def _resolve_password() -> str:
    if DB_SECRET_ARN:
        client = boto3.client("secretsmanager", **boto3_client_kwargs())
        secret = client.get_secret_value(SecretId=DB_SECRET_ARN)
        payload = json.loads(secret["SecretString"])
        return payload["password"]
    return DB_PASSWORD


def get_connection():
    """Open a new psycopg2 connection using this module's config. One
    connection per request/invocation -- same simplicity tradeoff as
    every other service's `db.py` (see their docstrings).
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=_resolve_password(),
    )


def create_api_key_for_checkout_session(
    conn,
    *,
    key_id: str,
    key_hash: str,
    pending_secret: str,
    owner_email: str,
    plan_tier: str,
    rate_limit: int,
    stripe_customer_id: str,
    stripe_checkout_session_id: str,
) -> bool:
    """Insert a new `api_keys` row for a completed self-serve Checkout
    Session. Returns True if a row was actually inserted, False if one
    already existed for this `stripe_checkout_session_id` (Stripe
    redelivers webhook events; this makes processing idempotent -- a
    redelivery is a no-op, not a second key/row).

    `ON CONFLICT (stripe_checkout_session_id) DO NOTHING` is the whole
    idempotency mechanism: the unique constraint the Phase 4 migration
    added on that column (see
    packages/shared-schema/alembic/versions/8ab25d318716_*.py) is what
    makes a redelivered event's insert attempt a guaranteed no-op
    instead of a race-prone "check then insert."  Deliberately does
    NOT regenerate a new secret/hash on conflict -- whichever row won
    the race keeps its own `pending_secret`, which may already have
    been retrieved and cleared (see `clear_pending_secret` /
    `find_api_key_by_checkout_session_id`); minting a second secret for
    an existing key would silently invalidate a secret the customer may
    already have saved.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_keys (
                key_id, owner_email, plan_tier, rate_limit, key_hash,
                active, stripe_customer_id, stripe_checkout_session_id,
                pending_secret, created_at
            )
            VALUES (
                %(key_id)s, %(owner_email)s, %(plan_tier)s, %(rate_limit)s, %(key_hash)s,
                true, %(stripe_customer_id)s, %(stripe_checkout_session_id)s,
                %(pending_secret)s, %(created_at)s
            )
            ON CONFLICT (stripe_checkout_session_id) DO NOTHING
            """,
            {
                "key_id": key_id,
                "owner_email": owner_email,
                "plan_tier": plan_tier,
                "rate_limit": rate_limit,
                "key_hash": key_hash,
                "stripe_customer_id": stripe_customer_id,
                "stripe_checkout_session_id": stripe_checkout_session_id,
                "pending_secret": pending_secret,
                "created_at": datetime.now(timezone.utc),
            },
        )
        inserted = cur.rowcount == 1
    conn.commit()
    return inserted


def find_api_key_by_checkout_session_id(conn, stripe_checkout_session_id: str) -> dict[str, Any] | None:
    """Backs `GET /v1/billing/session/{id}` -- looks the issued key up
    against OUR OWN idempotent webhook-created record rather than
    re-querying Stripe (see billing/routes.py's docstring on that
    choice).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM api_keys WHERE stripe_checkout_session_id = %s", (stripe_checkout_session_id,))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def clear_pending_secret(conn, key_id: str) -> None:
    """Clear a key's transient `pending_secret` after its first
    successful retrieval (see `GET /v1/billing/session/{id}`) -- see
    `shared_schema.models.ApiKey.pending_secret`'s docstring for why
    this exists and its known limitations (this is a best-effort
    "don't hand it out twice," not a strict one-time-token guarantee:
    e.g. two concurrent retrievals could both read it before either
    clears it).
    """
    with conn.cursor() as cur:
        cur.execute("UPDATE api_keys SET pending_secret = NULL WHERE key_id = %s", (key_id,))
    conn.commit()


def find_api_key_by_stripe_customer_id(conn, stripe_customer_id: str) -> dict[str, Any] | None:
    """Used by the subscription-lifecycle webhook handlers
    (`customer.subscription.updated`/`.deleted`) to find which key a
    Stripe customer's subscription change applies to.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM api_keys WHERE stripe_customer_id = %s", (stripe_customer_id,))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def find_latest_active_api_key_by_email(conn, owner_email: str) -> dict[str, Any] | None:
    """Used by `POST /v1/billing/portal` when a customer identifies
    themselves by email rather than API key. Picks the most recently
    created ACTIVE row for that email -- an email could in principle
    have issued multiple keys over time (e.g. re-subscribing after a
    cancellation); the most recent active one is the reasonable
    "current" subscription to manage.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT * FROM api_keys
            WHERE owner_email = %s AND active = true
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (owner_email,),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def find_api_key_by_hash(conn, key_hash: str) -> dict[str, Any] | None:
    """Used by `POST /v1/billing/portal` when a customer identifies
    themselves by API key rather than email. Deliberately a separate
    small function from api-service's own `fetch_api_key_by_hash` (same
    query, different service/module -- see this module's docstring on
    why each service gets its own copy).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM api_keys WHERE key_hash = %s", (key_hash,))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def update_subscription_status(
    conn,
    *,
    stripe_customer_id: str,
    active: bool,
    plan_tier: str | None = None,
    stripe_subscription_id: str | None = None,
) -> bool:
    """Apply a `customer.subscription.updated`/`.deleted` webhook to
    the matching `api_keys` row. Returns False (a no-op) if no row
    matches this customer -- that can legitimately happen for a
    subscription that was never tied to a key here, and should be
    logged/ignored by the caller, not treated as a hard error.

    `plan_tier`/`stripe_subscription_id` are only updated when
    provided (None leaves the existing value alone) -- `.deleted`
    events, for example, only ever need to flip `active` to False.
    """
    set_clauses = ["active = %(active)s"]
    params: dict[str, Any] = {"active": active, "stripe_customer_id": stripe_customer_id}
    if plan_tier is not None:
        set_clauses.append("plan_tier = %(plan_tier)s")
        params["plan_tier"] = plan_tier
    if stripe_subscription_id is not None:
        set_clauses.append("stripe_subscription_id = %(stripe_subscription_id)s")
        params["stripe_subscription_id"] = stripe_subscription_id

    query = f"UPDATE api_keys SET {', '.join(set_clauses)} WHERE stripe_customer_id = %(stripe_customer_id)s"
    with conn.cursor() as cur:
        cur.execute(query, params)
        updated = cur.rowcount > 0
    conn.commit()
    return updated


__all__ = [
    "clear_pending_secret",
    "create_api_key_for_checkout_session",
    "find_api_key_by_checkout_session_id",
    "find_api_key_by_hash",
    "find_api_key_by_stripe_customer_id",
    "find_latest_active_api_key_by_email",
    "get_connection",
    "update_subscription_status",
]
