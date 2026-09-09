"""An in-memory stand-in for parser/db.py's `fetch_domain`/`upsert_domain`
pair, used by tests/test_handler.py so handler-level tests don't need a
real Postgres connection. tests/test_db.py separately proves the real
psycopg2-backed implementation against a real local Postgres.
"""

from __future__ import annotations

from typing import Any


class FakeDomainStore:
    """Keyed by domain, holding whatever `upsert_domain` last wrote --
    same round-trip contract as the real table (a dict in, the same
    shape of dict back out via `fetch_domain`).
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def fetch(self, _conn, domain: str) -> dict[str, Any] | None:
        row = self.rows.get(domain)
        return dict(row) if row is not None else None

    def upsert(self, _conn, record: dict[str, Any]) -> None:
        self.rows[record["domain"]] = dict(record)
