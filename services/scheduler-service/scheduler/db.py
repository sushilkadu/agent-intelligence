"""Runtime Postgres access for scheduler-service: read-only join of
`domains` against `monitors`/`api_keys` to determine, per domain, which
active plan tiers (if any) are watching it.

Deliberately plain psycopg2, mirroring every other DB-touching
service's `db.py` in this repo.
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
    other service in this repo makes.
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=_resolve_password(),
    )


def fetch_domains_with_watcher_tiers(conn) -> list[dict[str, Any]]:
    """Return one row per known domain: `domain`, `last_crawled_at`, and
    `tiers` -- the set of DISTINCT active plan tiers among the keys
    that own a monitor on that domain (empty for a domain nobody is
    monitoring).

    A `LEFT JOIN` (not an inner join) is essential here: a domain with
    no monitors at all must still appear (with an empty `tiers` set) so
    it gets the default weekly cadence, not silently disappear from
    scheduling entirely.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                d.domain AS domain,
                d.last_crawled_at AS last_crawled_at,
                ak.plan_tier AS plan_tier
            FROM domains d
            LEFT JOIN monitors m ON m.domain = d.domain
            LEFT JOIN api_keys ak ON ak.key_id = m.owner_key_id AND ak.active = true
            ORDER BY d.domain
            """
        )
        rows = cur.fetchall()

    by_domain: dict[str, dict[str, Any]] = {}
    for row in rows:
        domain = row["domain"]
        entry = by_domain.setdefault(
            domain, {"domain": domain, "last_crawled_at": row["last_crawled_at"], "tiers": set()}
        )
        if row["plan_tier"] is not None:
            entry["tiers"].add(row["plan_tier"])

    return list(by_domain.values())


__all__ = ["fetch_domains_with_watcher_tiers", "get_connection"]
