"""Per-source-IP rate limiting for the free-tier public lookup API.

--- Why a Lambda-level token bucket instead of API Gateway usage plans ---

The build plan offers two options: "API Gateway usage plans or a
Lambda-level token bucket keyed by IP." Usage plans + API keys are a
REST API (API Gateway v1) concept; this architecture specifies an HTTP
API (API Gateway v2, see `infra/terraform/modules/api_gateway/main.tf`),
which has no usage-plan/API-key equivalent. API keys are also
explicitly Phase 4's job (tiered, paid rate limits) -- building them
now would preempt that phase's design. So: a Lambda-level limiter here,
scoped only to the free tier's single flat rate.

--- Why DynamoDB instead of an in-memory counter -------------------------

Lambda instances are stateless and horizontally scaled -- two
concurrent invocations (even for the same IP) may land on two
different execution environments with no shared memory, so an
in-process counter would undercount and let callers exceed the limit.
A small DynamoDB table gives real shared state across every
invocation, with on-demand billing (no capacity to provision for a
bursty public endpoint) and a TTL attribute so old windows clean
themselves up (see `infra/terraform/modules/dynamodb`).

--- Design: fixed-window counter, not a "true" token bucket --------------

The build plan calls this a "token bucket" loosely; what's implemented
is a fixed-window counter (one item per `{ip}#{window_start}`,
incremented atomically via `UpdateExpression="ADD ..."`), which is
simpler to reason about and just as effective for a flat free-tier
rate: it can't under-count under concurrent requests (DynamoDB's `ADD`
is atomic server-side), and it self-resets every `window_seconds`
because each window gets a fresh item key. A sliding-window/leaky-bucket
refinement is one place Phase 4 could tighten this up when it adds
tiered limits -- see repo-root Phase 3 report for the flag.

--- Extracting the client IP --------------------------------------------

Route handlers call this with `request.client.host` (a plain FastAPI
`Request`), not a raw Lambda event. That's not a shortcut around the
plan's guidance -- it's how the plan's own suggested field reaches
the route code: Mangum's API Gateway v2 (HTTP API) adapter builds the
ASGI scope's `client` tuple directly from
`event["requestContext"]["http"]["sourceIp"]` (verified against the
installed `mangum` package: `mangum/handlers/api_gateway.py`), so
`request.client.host` *is* `sourceIp` when running under Lambda, and
is the real local client IP when running under `uvicorn` for local
dev/testing -- one code path for both environments, per this phase's
"don't fork the route logic" requirement for the ASGI adapter.

This means `sourceIp` is used as-is (the direct caller's IP). Per the
build plan: if CloudFront ever fronts API Gateway, `sourceIp` would
become CloudFront's own IP, not the end user's, and the standard
forwarded-for header CloudFront sets should be preferred instead.
Phase 3 does not put CloudFront in front of API Gateway (see the
`cloudfront` Terraform module and the Phase 3 report for why), so
`sourceIp` alone is correct for now -- revisit this the moment
CloudFront (or any other reverse proxy) is added in front of this API.
"""

from __future__ import annotations

import time
from typing import Any

import boto3

from .config import (
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_TABLE_NAME,
    RATE_LIMIT_WINDOW_SECONDS,
    boto3_client_kwargs,
)


def get_dynamodb_client():
    return boto3.client("dynamodb", **boto3_client_kwargs())


def _window_item_id(ip: str, window_seconds: int, now: float) -> str:
    window_start = int(now // window_seconds) * window_seconds
    return f"{ip}#{window_start}"


def check_and_increment(
    client,
    table_name: str = RATE_LIMIT_TABLE_NAME,
    ip: str = "unknown",
    *,
    limit: int = RATE_LIMIT_PER_MINUTE,
    window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    now: float | None = None,
) -> tuple[bool, int]:
    """Atomically record one request from `ip` in its current fixed
    window and report whether it's still within `limit`.

    Returns `(allowed, count_after_this_request)`. Uses a single
    atomic `ADD` update so concurrent Lambda invocations for the same
    IP can never under-count each other -- correctness relies on
    DynamoDB doing the increment server-side, not on any
    read-then-write logic here.
    """
    now = now if now is not None else time.time()
    item_id = _window_item_id(ip, window_seconds, now)
    # TTL comfortably past this window's natural end -- generous
    # buffer since DynamoDB's TTL sweep is background/best-effort
    # (can lag real time by minutes), not something correctness
    # depends on: a new window always gets a new item key regardless
    # of whether the previous window's item has been swept yet.
    expires_at = int(now) + (window_seconds * 2)

    response = client.update_item(
        TableName=table_name,
        Key={"id": {"S": item_id}},
        UpdateExpression="ADD request_count :incr SET expires_at = if_not_exists(expires_at, :ttl)",
        ExpressionAttributeValues={":incr": {"N": "1"}, ":ttl": {"N": str(expires_at)}},
        ReturnValues="UPDATED_NEW",
    )
    count = int(response["Attributes"]["request_count"]["N"])
    return count <= limit, count


def build_rate_limited_error(*, limit: int, window_seconds: int, tier: str = "free") -> dict[str, Any]:
    """The clean 429 error body returned when a caller exceeds their
    tier's limit (free-tier IP limiting, or an authenticated key's own
    `rate_limit` -- see Phase 4's `check_and_increment_for_key`).
    """
    return {
        "error": "rate_limit_exceeded",
        "message": (
            f"Rate limit exceeded: this API allows {limit} requests per "
            f"{window_seconds} seconds on the {tier} tier. Please slow down and "
            "try again shortly."
        ),
    }


# --- Phase 4: authenticated (API-key) rate limiting -------------------------
#
# Reuses `check_and_increment` as-is rather than writing a parallel
# implementation -- the function was already generic on "an identifier
# string to key the fixed window by" (its `ip` parameter), just named
# for its one Phase 3 caller. The only thing Phase 4 needs is a
# different identifier (`key_id`) and that key's own `rate_limit`
# instead of the flat free-tier default, both of which the existing
# signature already accepts. A `key:` prefix on the item id keeps an
# authenticated caller's window in a distinct DynamoDB item from any
# IP-keyed window, so a key_id that happened to collide with someone's
# IP string could never share a counter.
def _key_item_subject(key_id: str) -> str:
    return f"key:{key_id}"


def check_and_increment_for_key(
    client,
    table_name: str,
    key_id: str,
    *,
    limit: int,
    window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    now: float | None = None,
) -> tuple[bool, int]:
    """Same fixed-window counter as `check_and_increment`, keyed by an
    authenticated API key's `key_id` (see `api/auth.py`) instead of a
    caller's IP, and enforced against that key's own `rate_limit`
    (looked up from its `api_keys` row) instead of the free-tier flat
    rate.
    """
    return check_and_increment(
        client,
        table_name,
        _key_item_subject(key_id),
        limit=limit,
        window_seconds=window_seconds,
        now=now,
    )


def get_current_window_count(
    client,
    table_name: str,
    key_id: str,
    *,
    window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    now: float | None = None,
) -> int:
    """Read (without incrementing) how many requests an API key has
    made in its *current* fixed window -- backs the dashboard's
    "current window usage" display (`GET /v1/keys/me`). A plain
    `GetItem`, not the atomic `ADD` used by `check_and_increment*`,
    since this deliberately does not count as a request itself.
    """
    now = now if now is not None else time.time()
    item_id = _window_item_id(_key_item_subject(key_id), window_seconds, now)
    response = client.get_item(TableName=table_name, Key={"id": {"S": item_id}})
    item = response.get("Item")
    if item is None:
        return 0
    return int(item["request_count"]["N"])


__all__ = [
    "build_rate_limited_error",
    "check_and_increment",
    "check_and_increment_for_key",
    "get_current_window_count",
    "get_dynamodb_client",
]
