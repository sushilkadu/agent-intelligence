"""Runtime Postgres access for api-service: read-only lookups against
`domains`.

Deliberately plain psycopg2, mirroring parser-service's
`parser/db.py` (its read side specifically -- `fetch_domain` here is
line-for-line the same query). api-service is a read-only consumer of
`domains`: it never writes to it (parser-service owns that upsert),
so there is no `upsert_domain` equivalent here.

Not importing parser-service's `db.py` directly -- these are separate
deployable services with separate dependency/packaging boundaries (a
parser-service code change should never be able to break api-service
at import time), so api-service gets its own small, independent copy
of this pattern instead of a cross-service import.
"""

from __future__ import annotations

import json
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
    """Resolve the DB password: Secrets Manager in real deployments
    (DB_SECRET_ARN set by Terraform), otherwise the plaintext
    DB_PASSWORD env var for local dev against docker-compose Postgres.
    """
    if DB_SECRET_ARN:
        client = boto3.client("secretsmanager", **boto3_client_kwargs())
        secret = client.get_secret_value(SecretId=DB_SECRET_ARN)
        payload = json.loads(secret["SecretString"])
        return payload["password"]
    return DB_PASSWORD


def get_connection():
    """Open a new psycopg2 connection using this module's config.

    One connection per request/invocation, same simplicity tradeoff as
    parser-service's `get_connection` -- pooling across warm Lambda
    invocations is a follow-up optimization, not a correctness
    requirement for Phase 3.
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=_resolve_password(),
    )


def fetch_domain(conn, domain: str) -> dict[str, Any] | None:
    """Return the `domains` row for `domain`, or None if it has never
    been crawled.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM domains WHERE domain = %s", (domain,))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def fetch_domains_bulk(conn, domains: list[str]) -> dict[str, dict[str, Any]]:
    """Return every `domains` row matching `domains`, keyed by domain.

    A domain with no row simply has no key in the returned dict --
    callers (see `api/routes.py`'s bulk endpoint) turn that into a
    per-domain "not found" marker rather than a request-level 404,
    since a bulk lookup is expected to mix hits and misses.
    """
    if not domains:
        return {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM domains WHERE domain = ANY(%s)", (domains,))
        rows = cur.fetchall()
        return {row["domain"]: dict(row) for row in rows}


def fetch_api_key_by_hash(conn, key_hash: str) -> dict[str, Any] | None:
    """Return the `api_keys` row whose `key_hash` matches, or None.

    api-service never writes to `api_keys` (billing-service owns
    issuance/lifecycle writes -- see services/billing-service/billing/db.py)
    -- this is a read-only lookup, same read-only relationship
    api-service already has with `domains`.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM api_keys WHERE key_hash = %s", (key_hash,))
        row = cur.fetchone()
        return dict(row) if row is not None else None


__all__ = ["fetch_api_key_by_hash", "fetch_domain", "fetch_domains_bulk", "get_connection"]
