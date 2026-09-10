"""Runtime Postgres access for notifier-service: read-only lookup of
`monitors` by domain.

Deliberately plain psycopg2, mirroring api-service's/parser-service's
`db.py` modules -- notifier-service is a read-only consumer of
`monitors` (it never creates/deletes rows; that's api-service's job via
`POST /v1/monitors`/`DELETE /v1/monitors/{id}`).
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
    if DB_SECRET_ARN:
        client = boto3.client("secretsmanager", **boto3_client_kwargs())
        secret = client.get_secret_value(SecretId=DB_SECRET_ARN)
        payload = json.loads(secret["SecretString"])
        return payload["password"]
    return DB_PASSWORD


def get_connection():
    """Open a new psycopg2 connection using this module's config. One
    connection per Lambda invocation, same simplicity tradeoff every
    other service in this repo makes (see parser/db.py's docstring).
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=_resolve_password(),
    )


def fetch_monitors_for_domain(conn, domain: str) -> list[dict[str, Any]]:
    """Return every `monitors` row watching `domain` -- who to notify
    (webhook_url) and who owns each monitor (owner_key_id, carried
    through into the outbound payload for the customer's own
    bookkeeping, though not used by notifier-service itself for
    anything beyond that).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM monitors WHERE domain = %s", (domain,))
        rows = cur.fetchall()
        return [dict(row) for row in rows]


__all__ = ["fetch_monitors_for_domain", "get_connection"]
