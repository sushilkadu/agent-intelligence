"""Public v1 routes: domain lookup + history.

Both routes are unauthenticated (Phase 4 adds API-key auth) and
free-tier rate limited per source IP (see `api/ratelimit.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .config import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_TABLE_NAME,
    RATE_LIMIT_WINDOW_SECONDS,
    RAW_DATA_BUCKET_NAME,
)
from .db import fetch_domain, get_connection
from .history import get_s3_client, list_domain_history
from .ratelimit import (
    build_rate_limited_error,
    check_and_increment,
    get_dynamodb_client,
)
from .schemas import DomainHistoryResponse

router = APIRouter()


def _get_db_connection():
    """FastAPI dependency: one Postgres connection per request, closed
    when the request finishes (mirrors parser-service's
    one-connection-per-invocation pattern -- see `api/db.py`).
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def enforce_rate_limit(request: Request) -> None:
    """FastAPI dependency enforcing the free-tier per-IP rate limit.

    See `api/ratelimit.py`'s module docstring for why
    `request.client.host` is the right IP to key on under both
    `uvicorn` (local dev) and Lambda (via Mangum).
    """
    client_ip = request.client.host if request.client else "unknown"
    dynamodb_client = get_dynamodb_client()
    allowed, _count = check_and_increment(
        dynamodb_client,
        RATE_LIMIT_TABLE_NAME,
        client_ip,
        limit=RATE_LIMIT_PER_MINUTE,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=build_rate_limited_error(limit=RATE_LIMIT_PER_MINUTE, window_seconds=RATE_LIMIT_WINDOW_SECONDS),
        )


@router.get(
    "/v1/domains/{domain}",
    summary="Look up a domain's agent-identity signals",
    dependencies=[Depends(enforce_rate_limit)],
)
def get_domain(domain: str, conn=Depends(_get_db_connection)) -> dict:  # noqa: B008 (standard FastAPI DI idiom)
    """Return the current normalized record for `domain`.

    404 (not 500) when the domain has never been crawled -- that's an
    expected, common outcome for a public lookup tool, not a server
    error.
    """
    row = fetch_domain(conn, domain)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "domain_not_found",
                "message": f"'{domain}' has not been crawled yet.",
            },
        )
    # See api/schemas.py's docstring: the response shape is exactly
    # the canonical `Domain` model's fields, which is exactly what
    # `fetch_domain`'s RealDictCursor row already looks like.
    return row


@router.get(
    "/v1/domains/{domain}/history",
    summary="List a domain's past crawl snapshots",
    response_model=DomainHistoryResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
def get_domain_history(
    domain: str,
    limit: int = Query(
        HISTORY_DEFAULT_LIMIT,
        ge=1,
        le=HISTORY_MAX_LIMIT,
        description="Max number of most-recent snapshots to return.",
    ),
) -> DomainHistoryResponse:
    """List `domain`'s past crawl snapshots, most-recent-first.

    See `api/history.py`'s module docstring for why this is served
    directly from the crawler's raw S3 bucket rather than a normalized
    history table, and how `limit` bounds it.
    """
    s3_client = get_s3_client()
    snapshots = list_domain_history(s3_client, RAW_DATA_BUCKET_NAME, domain, limit=limit)
    return DomainHistoryResponse(domain=domain, count=len(snapshots), limit=limit, snapshots=snapshots)


__all__ = ["router"]
