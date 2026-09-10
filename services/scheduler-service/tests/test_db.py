"""Exercises the real psycopg2-backed `fetch_domains_with_watcher_tiers`
join against a real local Postgres (docker-compose's `postgres`
service), instead of mocks -- proves the actual SQL (and the
Alembic-migrated `domains`/`monitors`/`api_keys` tables it joins) works,
not just that the Python logic around it is internally consistent.

Requires:
    docker compose up -d postgres
    (packages/shared-schema)$ DATABASE_URL=... alembic upgrade head

Skipped (not failed) when Postgres isn't reachable, mirroring
parser-service's tests/test_db.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import pytest

from scheduler import db

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


def _make_connection():
    try:
        conn = db.get_connection()
    except psycopg2.OperationalError:
        pytest.skip("local Postgres is not reachable (docker compose up -d postgres)")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.monitors')")
            if cur.fetchone()[0] is None:
                conn.close()
                pytest.skip("monitors table does not exist (run: alembic upgrade head)")
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


def _unique_domain(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}.test"


def _insert_domain(conn, domain: str, last_crawled_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO domains (
                domain, first_seen_at, last_crawled_at, agent_json_present, llms_txt_present,
                web_bot_auth_present, web_bot_auth_valid, declared_capabilities, confidence_flags,
                created_at, updated_at
            ) VALUES (%s, %s, %s, false, false, false, false, '{}'::jsonb, '{}', %s, %s)
            """,
            (domain, last_crawled_at, last_crawled_at, last_crawled_at, last_crawled_at),
        )


def _insert_api_key(conn, key_id: str, plan_tier: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_keys (key_id, owner_email, plan_tier, rate_limit, key_hash, active, created_at)
            VALUES (%s, %s, %s, 1000, %s, true, %s)
            """,
            (key_id, f"{key_id}@example.com", plan_tier, f"hash-{key_id}", NOW),
        )


def _insert_monitor(conn, domain: str, owner_key_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO monitors (monitor_id, domain, webhook_url, owner_key_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), domain, "https://hooks.example.com/x", owner_key_id, NOW),
        )


def test_domain_with_a_licensing_monitor_reports_the_licensing_tier(conn):
    domain = _unique_domain("licensing")
    key_id = f"key-{uuid.uuid4().hex[:8]}"
    last_crawled = NOW - timedelta(days=2)

    _insert_domain(conn, domain, last_crawled)
    _insert_api_key(conn, key_id, "licensing")
    _insert_monitor(conn, domain, key_id)
    conn.commit()

    rows = {row["domain"]: row for row in db.fetch_domains_with_watcher_tiers(conn)}
    assert domain in rows
    assert rows[domain]["tiers"] == {"licensing"}
    assert rows[domain]["last_crawled_at"] == last_crawled


def test_domain_with_no_monitors_reports_an_empty_tier_set(conn):
    domain = _unique_domain("nomonitor")
    last_crawled = NOW - timedelta(days=10)
    _insert_domain(conn, domain, last_crawled)
    conn.commit()

    rows = {row["domain"]: row for row in db.fetch_domains_with_watcher_tiers(conn)}
    assert domain in rows
    assert rows[domain]["tiers"] == set()


def test_domain_with_an_inactive_monitor_owner_key_is_excluded_from_tiers(conn):
    """An inactive key (e.g. a canceled subscription) must not count as
    "still watching" for cadence purposes -- the join filters on
    `ak.active = true`.
    """
    domain = _unique_domain("inactive")
    key_id = f"key-{uuid.uuid4().hex[:8]}"
    _insert_domain(conn, domain, NOW - timedelta(days=10))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys (key_id, owner_email, plan_tier, rate_limit, key_hash, active, created_at) "
            "VALUES (%s, %s, %s, 1000, %s, false, %s)",
            (key_id, f"{key_id}@example.com", "licensing", f"hash-{key_id}", NOW),
        )
    _insert_monitor(conn, domain, key_id)
    conn.commit()

    rows = {row["domain"]: row for row in db.fetch_domains_with_watcher_tiers(conn)}
    assert rows[domain]["tiers"] == set()
