"""Response models for api-service's public endpoints.

`GET /v1/domains/{domain}` intentionally reuses `shared_schema.Domain`
as-is for its response shape rather than defining a bespoke API model:
the canonical `Domain` Pydantic model's fields already line up
field-for-field with the `domains` table row `api/db.py` fetches (see
`packages/shared-schema/shared_schema/models.py`), so introducing a
second, slightly-different shape here would just be a translation
layer with no real benefit. This does mean internal-looking fields
like `agent_json_s3_key`/`web_bot_auth_key_id` are part of the public
response -- that's an accepted tradeoff for Phase 3 (the S3 keys don't
grant any access on their own; the bucket stays fully private, see
`infra/terraform/modules/s3/main.tf`'s public-access-block), not an
oversight. Revisit if Phase 4's paid tiers want a leaner/public vs.
internal field split.

`GET /v1/domains/{domain}/history` has no equivalent canonical model
(it's synthesized from S3 listings, not a DB row -- see
`api/history.py`), so it gets its own small response models here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .config import BULK_MAX_DOMAINS


class HistoryArtifactSummary(BaseModel):
    """Lightweight, unparsed metadata for one artifact (agents.json or
    the Web Bot Auth directory) as fetched in one historical crawl.
    Deliberately just the envelope's bookkeeping fields -- see
    `api/history.py`'s docstring for why this endpoint doesn't
    re-parse/re-validate full artifact content.
    """

    present: bool
    status_code: int | None = None
    fetched_at: str | None = None


class HistorySnapshot(BaseModel):
    """One historical crawl of a domain."""

    crawled_at: str = Field(..., description="ISO-8601 timestamp this crawl ran at")
    agents_json: HistoryArtifactSummary
    web_bot_auth: HistoryArtifactSummary


class DomainHistoryResponse(BaseModel):
    domain: str
    count: int = Field(..., description="Number of snapshots in this response (<= the requested limit)")
    limit: int = Field(..., description="The limit applied to this request")
    snapshots: list[HistorySnapshot]


class BulkDomainsRequest(BaseModel):
    """`POST /v1/domains/bulk` request body. `domains` is capped at
    `BULK_MAX_DOMAINS` (see api/config.py's docstring on why 100) --
    pydantic rejects an over-cap list with a 422 before the route body
    ever runs, so there's no path where an unbounded list reaches the
    DB query.
    """

    domains: list[str] = Field(
        ...,
        min_length=1,
        max_length=BULK_MAX_DOMAINS,
        description=f"Domains to look up, 1-{BULK_MAX_DOMAINS} per request.",
    )


class BulkDomainResult(BaseModel):
    """One domain's result within a bulk response -- a per-domain
    not-found marker (`found=False`, `record=None`), never a
    request-level 404, since a bulk request mixing hits and misses is
    the expected case.
    """

    domain: str
    found: bool
    record: dict[str, Any] | None = None


class BulkDomainsResponse(BaseModel):
    count: int = Field(..., description="Number of results in this response (== len(domains) requested)")
    results: list[BulkDomainResult]


class KeyUsageResponse(BaseModel):
    """`GET /v1/keys/me` -- backs the dashboard's usage panel."""

    key_id: str
    plan_tier: str
    rate_limit: int = Field(..., description="Requests allowed per window for this key")
    window_seconds: int
    current_window_count: int = Field(..., description="Requests this key has made in the CURRENT window so far")


__all__ = [
    "BulkDomainResult",
    "BulkDomainsRequest",
    "BulkDomainsResponse",
    "DomainHistoryResponse",
    "HistoryArtifactSummary",
    "HistorySnapshot",
    "KeyUsageResponse",
]
