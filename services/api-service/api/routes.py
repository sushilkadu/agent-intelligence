"""Public v1 routes: domain lookup, history, bulk lookup, and key usage.

The two original Phase 3 routes (`GET /v1/domains/{domain}`,
`GET /v1/domains/{domain}/history`) are unauthenticated-by-default and
free-tier rate limited per source IP -- Phase 4 makes them
*optionally* authenticated: send a valid `X-API-Key` header and you're
rate-limited at your key's own tier instead of the free-tier IP rate; a
present-but-invalid key is a clean 401; no header at all is exactly
Phase 3's original behavior, unchanged. See `api/auth.py` and
`api/ratelimit.py`'s module docstrings for the underlying design.

`POST /v1/domains/bulk` and `GET /v1/keys/me` are new Phase 4 routes
that always require a valid API key (see their own dependencies below).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import ApiKeyContext, authenticate, lookup_api_key_context
from .config import (
    API_KEY_HEADER,
    BULK_ALLOWED_PLAN_TIERS,
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_TABLE_NAME,
    RATE_LIMIT_WINDOW_SECONDS,
    RAW_DATA_BUCKET_NAME,
)
from .db import fetch_domain, fetch_domains_bulk, get_connection
from .history import get_s3_client, list_domain_history
from .ratelimit import (
    build_rate_limited_error,
    check_and_increment,
    check_and_increment_for_key,
    get_current_window_count,
    get_dynamodb_client,
)
from .schemas import (
    BulkDomainResult,
    BulkDomainsRequest,
    BulkDomainsResponse,
    DomainHistoryResponse,
    KeyUsageResponse,
)

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
    """FastAPI dependency enforcing rate limiting for the two original
    public GET routes (and, since it's the same generic dependency,
    `GET /v1/keys/me` -- see below).

    Two paths, selected by whether `X-API-Key` is present at all:

      * No header -- Phase 3's original free-tier behavior, byte for
        byte: per-source-IP fixed-window counter against
        `RATE_LIMIT_PER_MINUTE`. Deliberately opens NO database
        connection on this path (see below) so a caller who never
        sends a key never pays for one, and this service's existing
        unit tests (which mock only DynamoDB/S3, never a real
        Postgres) keep working unmodified.
      * Header present -- must hash to an `active` `api_keys` row (a
        401 `invalid_api_key` if not, see `api/auth.py`), then the
        fixed-window counter is keyed by that key's `key_id` and
        enforced against ITS OWN `rate_limit`, not the free-tier flat
        rate.

    A connection is opened directly here (not via the `_get_db_connection`
    FastAPI dependency) specifically so it's only ever opened on the
    header-present path -- `Depends(_get_db_connection)` would open one
    unconditionally for every call to this dependency, including the
    common no-key case this service is optimized for.
    """
    header_value = request.headers.get(API_KEY_HEADER)
    dynamodb_client = get_dynamodb_client()

    if header_value:
        conn = get_connection()
        try:
            context = authenticate(conn, header_value)
        finally:
            conn.close()
        assert context is not None  # authenticate() raises rather than returning None here
        allowed, _count = check_and_increment_for_key(
            dynamodb_client,
            RATE_LIMIT_TABLE_NAME,
            context.key_id,
            limit=context.rate_limit,
            window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=build_rate_limited_error(
                    limit=context.rate_limit, window_seconds=RATE_LIMIT_WINDOW_SECONDS, tier=context.plan_tier
                ),
            )
        return

    client_ip = request.client.host if request.client else "unknown"
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


def require_any_api_key(request: Request, conn=Depends(_get_db_connection)) -> ApiKeyContext:  # noqa: B008 (standard FastAPI DI idiom)
    """FastAPI dependency for routes that always need SOME valid key
    (any plan tier) -- currently just `GET /v1/keys/me`. Missing header
    -> 401 `missing_api_key`; present-but-invalid -> 401
    `invalid_api_key` (via `authenticate`).
    """
    header_value = request.headers.get(API_KEY_HEADER)
    context = authenticate(conn, header_value)
    if context is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "missing_api_key", "message": f"This endpoint requires an {API_KEY_HEADER} header."},
        )
    return context


def require_bulk_api_key(request: Request, conn=Depends(_get_db_connection)) -> ApiKeyContext:  # noqa: B008 (standard FastAPI DI idiom)
    """FastAPI dependency for `POST /v1/domains/bulk`: requires a valid,
    active, `self_serve`/`licensing`-tier key.

    Deliberately collapses "no key", "key doesn't match any active
    row", and "valid key but `free`-tier" into the SAME clean 403 --
    per the build plan, this is an authorization failure (you may not
    call this endpoint), not an authentication one, so it doesn't
    distinguish those cases the way `enforce_rate_limit`'s 401 does for
    the general routes. `free`-tier keys can't exist in practice
    (free tier is the unauthenticated IP-limited path -- see
    `api/config.py`'s `BULK_ALLOWED_PLAN_TIERS`), but the tier check is
    kept explicit rather than assumed, so a future tier or a
    provisioning bug can't silently grant bulk access.
    """
    header_value = request.headers.get(API_KEY_HEADER)
    context = lookup_api_key_context(conn, header_value)
    if context is None or context.plan_tier not in BULK_ALLOWED_PLAN_TIERS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": (
                    "POST /v1/domains/bulk requires an active API key on the self_serve or licensing plan. "
                    "See POST /v1/billing/checkout (billing-service) to subscribe."
                ),
            },
        )
    return context


def enforce_bulk_rate_limit(context: ApiKeyContext = Depends(require_bulk_api_key)) -> None:  # noqa: B008 (standard FastAPI DI idiom)
    """Rate-limits the bulk endpoint using the same key-based scheme as
    `enforce_rate_limit`'s authenticated path, just expressed as its own
    dependency since the bulk endpoint has no unauthenticated path to
    fall back to. Depends on `require_bulk_api_key` so FastAPI's
    per-request dependency caching means the key lookup happens once,
    not twice, per bulk request.
    """
    dynamodb_client = get_dynamodb_client()
    allowed, _count = check_and_increment_for_key(
        dynamodb_client,
        RATE_LIMIT_TABLE_NAME,
        context.key_id,
        limit=context.rate_limit,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=build_rate_limited_error(
                limit=context.rate_limit, window_seconds=RATE_LIMIT_WINDOW_SECONDS, tier=context.plan_tier
            ),
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


@router.post(
    "/v1/domains/bulk",
    summary="Look up multiple domains' agent-identity signals in one request",
    response_model=BulkDomainsResponse,
    dependencies=[Depends(enforce_bulk_rate_limit)],
)
def bulk_domains(
    payload: BulkDomainsRequest,
    conn=Depends(_get_db_connection),  # noqa: B008 (standard FastAPI DI idiom)
) -> BulkDomainsResponse:
    """Paid-tier bulk lookup: one row (found or not-found) per
    requested domain, in one response -- never a request-level 404,
    since a bulk request mixing hits and misses is the expected case
    (see `api/db.py`'s `fetch_domains_bulk`).
    """
    rows_by_domain = fetch_domains_bulk(conn, payload.domains)
    results = [
        BulkDomainResult(domain=domain, found=domain in rows_by_domain, record=rows_by_domain.get(domain))
        for domain in payload.domains
    ]
    return BulkDomainsResponse(count=len(results), results=results)


@router.get(
    "/v1/keys/me",
    summary="Look up the calling API key's plan tier, rate limit, and current usage",
    response_model=KeyUsageResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
def get_key_usage(context: ApiKeyContext = Depends(require_any_api_key)) -> KeyUsageResponse:  # noqa: B008 (standard FastAPI DI idiom)
    """Backs the dashboard's usage panel (nice-to-have, per the build
    plan): a key's own plan tier/rate limit, plus (best-effort) how
    many requests it's made in the CURRENT fixed rate-limit window.
    """
    dynamodb_client = get_dynamodb_client()
    current_count = get_current_window_count(
        dynamodb_client, RATE_LIMIT_TABLE_NAME, context.key_id, window_seconds=RATE_LIMIT_WINDOW_SECONDS
    )
    return KeyUsageResponse(
        key_id=context.key_id,
        plan_tier=context.plan_tier,
        rate_limit=context.rate_limit,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        current_window_count=current_count,
    )


__all__ = ["router"]
