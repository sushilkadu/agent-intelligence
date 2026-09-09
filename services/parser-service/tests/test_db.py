"""Exercises the real psycopg2-backed `fetch_domain`/`upsert_domain`
against a real local Postgres (docker-compose's `postgres` service --
see repo root docker-compose.yml), instead of mocks. This is the one
place Phase 2 proves the upsert SQL (and the Alembic-migrated
`domains` table it depends on) actually works, not just that the
Python logic around it is internally consistent.

Requires:
    docker compose up -d postgres          # from the repo root
    (packages/shared-schema)$ DATABASE_URL=... alembic upgrade head

Connection is configured via the same DB_HOST/DB_PORT/DB_NAME/DB_USER/
DB_PASSWORD env vars parser/config.py already reads (defaults match
docker-compose.yml's local Postgres exactly), so no test-specific
config is needed when running against the standard local setup.

Skipped (not failed) when Postgres isn't reachable, so CI -- which
doesn't start docker-compose services -- doesn't fail on this file;
local verification runs it for real.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from parser import db

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _make_connection():
    try:
        conn = db.get_connection()
    except psycopg2.OperationalError:
        pytest.skip("local Postgres is not reachable (docker compose up -d postgres)")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.domains')")
            if cur.fetchone()[0] is None:
                conn.close()
                pytest.skip("domains table does not exist (run: alembic upgrade head)")
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


def _sample_record(domain: str, **overrides) -> dict:
    record = {
        "domain": domain,
        "first_seen_at": NOW,
        "last_crawled_at": NOW,
        "agent_json_present": True,
        "agent_json_s3_key": f"{domain}/2026-09-09T12:00:00+00:00/agents.json",
        "llms_txt_present": False,
        "llms_txt_s3_key": None,
        "web_bot_auth_present": True,
        "web_bot_auth_key_id": "key-1",
        "web_bot_auth_valid": True,
        "web_bot_auth_expiry": NOW + timedelta(days=30),
        "declared_capabilities": {"agents": [{"name": "demo-bot"}]},
        "confidence_flags": [],
        "on_chain_ref": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    record.update(overrides)
    return record


def _unique_domain(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}.test"


def test_fetch_domain_returns_none_when_absent(conn):
    assert db.fetch_domain(conn, "definitely-not-a-real-domain.test") is None


def test_upsert_then_fetch_round_trips(conn):
    domain = _unique_domain("roundtrip")
    record = _sample_record(domain)

    db.upsert_domain(conn, record)
    fetched = db.fetch_domain(conn, domain)

    assert fetched is not None
    assert fetched["domain"] == domain
    assert fetched["declared_capabilities"] == record["declared_capabilities"]
    assert fetched["confidence_flags"] == []
    assert fetched["web_bot_auth_key_id"] == "key-1"


def test_upsert_preserves_first_seen_at_and_created_at_across_updates(conn):
    domain = _unique_domain("preserve")
    original_first_seen = NOW
    original_created = NOW

    db.upsert_domain(conn, _sample_record(domain, first_seen_at=original_first_seen, created_at=original_created))

    later = NOW + timedelta(days=5)
    # A second crawl's record (as normalize.py would build it) claims a
    # *different* first_seen_at/created_at than what's already stored --
    # this must not overwrite the originals.
    db.upsert_domain(
        conn,
        _sample_record(
            domain,
            first_seen_at=later,
            created_at=later,
            last_crawled_at=later,
            updated_at=later,
            confidence_flags=["expired_key"],
        ),
    )

    fetched = db.fetch_domain(conn, domain)
    assert fetched["first_seen_at"] == original_first_seen
    assert fetched["created_at"] == original_created
    assert fetched["last_crawled_at"] == later
    assert fetched["updated_at"] == later
    assert fetched["confidence_flags"] == ["expired_key"]
