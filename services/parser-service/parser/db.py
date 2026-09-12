"""Runtime Postgres access for parser-service: fetch a domain's
previous row, upsert its new normalized record.

Deliberately plain psycopg2 + hand-written SQL, NOT the SQLAlchemy Core
metadata in `shared_schema.tables` -- that module exists purely to give
Alembic something to diff against (see its docstring). The runtime
upsert here is a single `INSERT ... ON CONFLICT (domain) DO UPDATE`
that excludes exactly two columns (`first_seen_at`, `created_at`) from
the update side; expressing that through SQLAlchemy Core's
`postgresql.insert(...).on_conflict_do_update(...)` construct would be
more indirection for no real benefit when the raw SQL is this short and
this stable. Lambda handlers are sync, so a sync driver (psycopg2) is
the right choice -- no need for asyncpg/async plumbing here.
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

# Columns in `domains`, in the order the upsert statement binds them.
# Kept as an explicit tuple (rather than deriving from the record dict)
# so a typo'd/extra key in a caller's record dict fails loudly instead
# of silently being dropped or breaking column alignment.
_DOMAIN_COLUMNS = (
    "domain",
    "first_seen_at",
    "last_crawled_at",
    "agent_json_present",
    "agent_json_s3_key",
    "llms_txt_present",
    "llms_txt_s3_key",
    "web_bot_auth_present",
    "web_bot_auth_key_id",
    "web_bot_auth_valid",
    "web_bot_auth_expiry",
    "declared_capabilities",
    "manifest_malformed_reason",
    "web_bot_auth_malformed_reason",
    "confidence_flags",
    "on_chain_ref",
    "created_at",
    "updated_at",
)

# Every column except the two preserved-on-conflict bookkeeping fields.
_UPDATE_COLUMNS = tuple(c for c in _DOMAIN_COLUMNS if c not in ("first_seen_at", "created_at"))


def _resolve_password() -> str:
    """Resolve the DB password: Secrets Manager in real deployments
    (DB_SECRET_ARN set by Terraform's `rds` module, which uses RDS's
    native `manage_master_user_password` -- see
    infra/terraform/modules/rds/main.tf), otherwise the plaintext
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

    One connection per Lambda invocation is deliberately simple for
    Phase 2's scope -- connection pooling / reuse across warm-start
    invocations is a follow-up optimization, not a correctness
    requirement.
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=_resolve_password(),
    )


def fetch_domain(conn, domain: str) -> dict[str, Any] | None:
    """Return the existing `domains` row for `domain`, or None if this
    domain has never been crawled before.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM domains WHERE domain = %s", (domain,))
        row = cur.fetchone()
        return dict(row) if row is not None else None


def upsert_domain(conn, record: dict[str, Any]) -> None:
    """Insert `record` into `domains`, or update the existing row by
    primary key (`domain`). `first_seen_at`/`created_at` are only ever
    written on the initial insert -- an update never overwrites them,
    regardless of what's in `record` (normalize.py already carries the
    previous row's values forward for exactly this reason; the SQL
    enforces it independently as a second line of defense).
    """
    columns_sql = ", ".join(_DOMAIN_COLUMNS)
    placeholders_sql = ", ".join(f"%({c})s" for c in _DOMAIN_COLUMNS)
    update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATE_COLUMNS)

    query = f"""
        INSERT INTO domains ({columns_sql})
        VALUES ({placeholders_sql})
        ON CONFLICT (domain) DO UPDATE SET {update_sql}
    """

    params = dict(record)
    params["declared_capabilities"] = psycopg2.extras.Json(record["declared_capabilities"])

    with conn.cursor() as cur:
        cur.execute(query, params)
    conn.commit()


__all__ = ["fetch_domain", "get_connection", "upsert_domain"]
