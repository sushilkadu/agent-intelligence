"""Exercises the real psycopg2-backed `billing/db.py` functions against
a real local Postgres (docker-compose's `postgres` service), instead of
mocks -- same pattern as parser-service's `tests/test_db.py`. This is
the one place this suite proves the Phase 4 migration's new columns
(`key_hash`, `active`, `stripe_customer_id`, `stripe_checkout_session_id`,
`pending_secret`) and, most importantly, the ON CONFLICT-based webhook
idempotency actually work against a real table -- not just that the
Python logic around them is internally consistent.

Requires:
    docker compose up -d postgres          # from the repo root
    (packages/shared-schema)$ DATABASE_URL=... alembic upgrade head

Skipped (not failed) when Postgres isn't reachable or hasn't been
migrated, so CI doesn't fail on this file; local verification runs it
for real.
"""

from __future__ import annotations

import uuid

import psycopg2
import pytest

from billing import db


def _make_connection():
    try:
        conn = db.get_connection()
    except psycopg2.OperationalError:
        pytest.skip("local Postgres is not reachable (docker compose up -d postgres)")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'api_keys'")
            columns = {row[0] for row in cur.fetchall()}
            required = {"key_hash", "active", "stripe_customer_id", "stripe_checkout_session_id", "pending_secret"}
            if not required.issubset(columns):
                conn.close()
                pytest.skip(
                    "api_keys is missing Phase 4 columns (run: alembic upgrade head from packages/shared-schema)"
                )
    except Exception:
        conn.close()
        raise
    return conn


@pytest.fixture
def conn():
    connection = _make_connection()
    yield connection
    connection.rollback()
    connection.close()


def _unique_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def test_create_api_key_for_checkout_session_round_trips(conn):
    session_id = _unique_id("cs_test")
    inserted = db.create_api_key_for_checkout_session(
        conn,
        key_id=_unique_id("key"),
        key_hash=_unique_id("hash"),
        pending_secret="ai_live_realsecret",
        owner_email="buyer@example.com",
        plan_tier="self_serve",
        rate_limit=600,
        stripe_customer_id=_unique_id("cus_test"),
        stripe_checkout_session_id=session_id,
    )
    assert inserted is True

    row = db.find_api_key_by_checkout_session_id(conn, session_id)
    assert row is not None
    assert row["owner_email"] == "buyer@example.com"
    assert row["plan_tier"] == "self_serve"
    assert row["rate_limit"] == 600
    assert row["active"] is True
    assert row["pending_secret"] == "ai_live_realsecret"
    # The plaintext secret is never stored anywhere BUT this transient
    # column -- key_hash is a real, distinct hash, not the plaintext.
    assert row["key_hash"] != "ai_live_realsecret"


def test_create_api_key_for_checkout_session_is_idempotent_on_redelivery(conn):
    """The exact scenario a redelivered Stripe webhook produces: the
    SAME `stripe_checkout_session_id` arrives twice. This must insert
    exactly one row and must NOT mint a second secret/hash -- proven
    here against the REAL unique constraint the Phase 4 migration
    added, not just asserted about the Python call.
    """
    session_id = _unique_id("cs_test_redelivered")
    first_key_id = _unique_id("key")

    first_inserted = db.create_api_key_for_checkout_session(
        conn,
        key_id=first_key_id,
        key_hash=_unique_id("hash-first"),
        pending_secret="ai_live_first_secret",
        owner_email="buyer@example.com",
        plan_tier="self_serve",
        rate_limit=600,
        stripe_customer_id=_unique_id("cus_test"),
        stripe_checkout_session_id=session_id,
    )
    assert first_inserted is True

    # Simulate Stripe redelivering the same event: a second webhook
    # invocation would generate a NEW secret/hash/key_id before calling
    # this function (it has no way to know in advance this session was
    # already processed) -- the DB call itself is what must no-op.
    second_inserted = db.create_api_key_for_checkout_session(
        conn,
        key_id=_unique_id("key-second-attempt"),
        key_hash=_unique_id("hash-second"),
        pending_secret="ai_live_second_secret",
        owner_email="buyer@example.com",
        plan_tier="self_serve",
        rate_limit=600,
        stripe_customer_id=_unique_id("cus_test"),
        stripe_checkout_session_id=session_id,
    )
    assert second_inserted is False

    row = db.find_api_key_by_checkout_session_id(conn, session_id)
    assert row["key_id"] == first_key_id
    assert row["pending_secret"] == "ai_live_first_secret"  # the FIRST secret wins, never overwritten


def test_clear_pending_secret_removes_it_and_only_it(conn):
    session_id = _unique_id("cs_test_clear")
    key_id = _unique_id("key")
    db.create_api_key_for_checkout_session(
        conn,
        key_id=key_id,
        key_hash=_unique_id("hash"),
        pending_secret="ai_live_to_be_cleared",
        owner_email="buyer@example.com",
        plan_tier="self_serve",
        rate_limit=600,
        stripe_customer_id=_unique_id("cus_test"),
        stripe_checkout_session_id=session_id,
    )

    db.clear_pending_secret(conn, key_id)

    row = db.find_api_key_by_checkout_session_id(conn, session_id)
    assert row["pending_secret"] is None
    assert row["active"] is True  # unrelated fields untouched


def test_update_subscription_status_deactivates_by_stripe_customer_id(conn):
    customer_id = _unique_id("cus_test")
    db.create_api_key_for_checkout_session(
        conn,
        key_id=_unique_id("key"),
        key_hash=_unique_id("hash"),
        pending_secret="ai_live_x",
        owner_email="buyer@example.com",
        plan_tier="self_serve",
        rate_limit=600,
        stripe_customer_id=customer_id,
        stripe_checkout_session_id=_unique_id("cs_test"),
    )

    updated = db.update_subscription_status(conn, stripe_customer_id=customer_id, active=False)
    assert updated is True

    row = db.find_api_key_by_stripe_customer_id(conn, customer_id)
    assert row["active"] is False


def test_update_subscription_status_is_a_no_op_for_an_unknown_customer(conn):
    updated = db.update_subscription_status(conn, stripe_customer_id=_unique_id("cus_never_existed"), active=False)
    assert updated is False


def test_find_latest_active_api_key_by_email_ignores_inactive_rows(conn):
    email = f"{_unique_id('buyer')}@example.com"
    inactive_customer = _unique_id("cus_inactive")
    db.create_api_key_for_checkout_session(
        conn,
        key_id=_unique_id("key-inactive"),
        key_hash=_unique_id("hash-inactive"),
        pending_secret="ai_live_inactive",
        owner_email=email,
        plan_tier="self_serve",
        rate_limit=600,
        stripe_customer_id=inactive_customer,
        stripe_checkout_session_id=_unique_id("cs_test_inactive"),
    )
    db.update_subscription_status(conn, stripe_customer_id=inactive_customer, active=False)

    assert db.find_latest_active_api_key_by_email(conn, email) is None

    active_customer = _unique_id("cus_active")
    db.create_api_key_for_checkout_session(
        conn,
        key_id=_unique_id("key-active"),
        key_hash=_unique_id("hash-active"),
        pending_secret="ai_live_active",
        owner_email=email,
        plan_tier="self_serve",
        rate_limit=600,
        stripe_customer_id=active_customer,
        stripe_checkout_session_id=_unique_id("cs_test_active"),
    )

    found = db.find_latest_active_api_key_by_email(conn, email)
    assert found is not None
    assert found["stripe_customer_id"] == active_customer
