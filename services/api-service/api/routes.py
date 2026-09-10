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

import uuid
from datetime import datetime, timezone

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from shared_utils import UnsafeWebhookURLError, validate_webhook_url

from .auth import ApiKeyContext, authenticate, lookup_api_key_context
from .config import (
    API_KEY_HEADER,
    BULK_ALLOWED_PLAN_TIERS,
    EXPORT_ALLOWED_PLAN_TIERS,
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    MONITOR_ALLOWED_PLAN_TIERS,
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_TABLE_NAME,
    RATE_LIMIT_WINDOW_SECONDS,
    RAW_DATA_BUCKET_NAME,
)
from .db import (
    delete_monitor,
    fetch_all_domains,
    fetch_domain,
    fetch_domains_bulk,
    fetch_monitor,
    get_connection,
    insert_monitor,
)
from .export import get_export_s3_client, run_export
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
    ExportResponse,
    KeyUsageResponse,
    MonitorCreateRequest,
    MonitorResponse,
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


def require_monitor_api_key(request: Request, conn=Depends(_get_db_connection)) -> ApiKeyContext:  # noqa: B008 (standard FastAPI DI idiom)
    """FastAPI dependency for `POST /v1/monitors`: monitoring is a paid
    feature, gated the exact same way `POST /v1/domains/bulk` is (see
    `require_bulk_api_key`'s docstring for the full 403-for-everything
    rationale) -- "no key", "unrecognized key", and "valid free-tier
    key" all collapse to one clean 403, never a 401/500.
    """
    header_value = request.headers.get(API_KEY_HEADER)
    context = lookup_api_key_context(conn, header_value)
    if context is None or context.plan_tier not in MONITOR_ALLOWED_PLAN_TIERS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": (
                    "POST /v1/monitors requires an active API key on the self_serve or licensing plan. "
                    "See POST /v1/billing/checkout (billing-service) to subscribe."
                ),
            },
        )
    return context


def enforce_monitor_rate_limit(context: ApiKeyContext = Depends(require_monitor_api_key)) -> None:  # noqa: B008 (standard FastAPI DI idiom)
    """Rate-limits monitor registration using the same key-based scheme
    as the bulk endpoint's `enforce_bulk_rate_limit` -- see that
    function's docstring.
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


def require_export_api_key(request: Request, conn=Depends(_get_db_connection)) -> ApiKeyContext:  # noqa: B008 (standard FastAPI DI idiom)
    """FastAPI dependency for `POST /v1/export`: a full-table dump is
    gated more strictly than bulk lookup/monitoring -- `licensing` tier
    only (see `EXPORT_ALLOWED_PLAN_TIERS`), same 403-for-everything
    shape as the other paid-tier dependencies above.
    """
    header_value = request.headers.get(API_KEY_HEADER)
    context = lookup_api_key_context(conn, header_value)
    if context is None or context.plan_tier not in EXPORT_ALLOWED_PLAN_TIERS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": "POST /v1/export requires an active API key on the licensing plan.",
            },
        )
    return context


@router.post(
    "/v1/monitors",
    summary="Register a webhook to be notified when a domain's agent-identity signals change",
    response_model=MonitorResponse,
    status_code=201,
    dependencies=[Depends(enforce_monitor_rate_limit)],
)
def create_monitor(
    payload: MonitorCreateRequest,
    context: ApiKeyContext = Depends(require_monitor_api_key),  # noqa: B008 (standard FastAPI DI idiom)
    conn=Depends(_get_db_connection),  # noqa: B008 (standard FastAPI DI idiom)
) -> MonitorResponse:
    """Create a monitor owned by the calling key.

    `webhook_url` is validated for SSRF safety BEFORE anything is
    persisted (see `shared_utils.webhook_safety.validate_webhook_url`'s
    docstring for exactly what "safe" means here and its documented
    DNS-rebinding residual risk) -- an unsafe URL is a clean 400, never
    a 500 and never a row that gets created anyway. This is
    defense-in-depth's FIRST layer; notifier-service re-validates the
    same URL again immediately before every delivery attempt (see
    `services/notifier-service/notifier/webhook.py`), since a URL safe
    at registration time is not guaranteed to still be safe at some
    future delivery time.
    """
    try:
        validate_webhook_url(payload.webhook_url)
    except UnsafeWebhookURLError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "unsafe_webhook_url", "message": str(exc)},
        ) from exc

    monitor_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)

    try:
        row = insert_monitor(
            conn,
            monitor_id=monitor_id,
            domain=payload.domain,
            webhook_url=payload.webhook_url,
            owner_key_id=context.key_id,
            created_at=created_at,
        )
    except psycopg2.IntegrityError as exc:
        # Almost certainly the `domain` FK constraint (monitors.domain
        # references domains.domain) -- registering a monitor for a
        # domain this service has never crawled. A clean 400, not a
        # 500: the caller sent a domain name this system doesn't
        # recognize, not something that broke.
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_domain",
                "message": f"'{payload.domain}' has not been crawled yet -- cannot register a monitor for it.",
            },
        ) from exc

    return MonitorResponse(
        monitor_id=str(row["monitor_id"]),
        domain=row["domain"],
        webhook_url=row["webhook_url"],
        owner_key_id=row["owner_key_id"],
        created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
    )


@router.delete(
    "/v1/monitors/{monitor_id}",
    summary="Delete a monitor you own",
    status_code=204,
)
def delete_monitor_route(
    monitor_id: str,
    context: ApiKeyContext = Depends(require_any_api_key),  # noqa: B008 (standard FastAPI DI idiom)
    conn=Depends(_get_db_connection),  # noqa: B008 (standard FastAPI DI idiom)
) -> None:
    """Delete `monitor_id` if (and only if) it's owned by the calling
    key.

    Returns 404 -- never a 403 -- for BOTH "no such monitor" AND "this
    monitor exists but belongs to a different key." Distinguishing
    those with a 403-vs-404 split would let a caller enumerate other
    customers' monitor ids by observing which status code comes back
    for a guessed id (403 confirms existence, 404 denies it); collapsing
    both to 404 makes that enumeration attack unobservable, at the
    standard cost of "not found" also technically describing "found,
    but not yours."
    """
    try:
        parsed_id = uuid.UUID(monitor_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "monitor_not_found", "message": f"'{monitor_id}' is not a valid monitor id."},
        ) from None

    row = fetch_monitor(conn, parsed_id)
    if row is None or row["owner_key_id"] != context.key_id:
        raise HTTPException(
            status_code=404,
            detail={"error": "monitor_not_found", "message": f"No monitor '{monitor_id}' found for this API key."},
        )

    delete_monitor(conn, parsed_id)
    return None


@router.post(
    "/v1/export",
    summary="Export the full domains table as NDJSON, licensing tier only",
    response_model=ExportResponse,
)
def export_domains(
    context: ApiKeyContext = Depends(require_export_api_key),  # noqa: B008 (standard FastAPI DI idiom)
    conn=Depends(_get_db_connection),  # noqa: B008 (standard FastAPI DI idiom)
) -> ExportResponse:
    """Dump every `domains` row to S3 as newline-delimited JSON and
    return a short-lived presigned GET URL for it (see api/export.py's
    module docstring for the full rationale, including why this runs
    synchronously in-request at THIS phase's scale and what changes
    before real production traffic: this needs to become an async,
    SQS-triggered export worker once the dataset is large enough that a
    synchronous dump risks the Lambda/API-Gateway request timeout).
    """
    rows = fetch_all_domains(conn)
    s3_client = get_export_s3_client()
    result = run_export(s3_client, rows, key_id=context.key_id)
    return ExportResponse(**result)


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
