"""Configuration constants for api-service.

Mirrors crawler-service's `crawler/config.py` / parser-service's
`parser/config.py` in style: centralize env var names in one place,
default to values that make local dev/testing work without extra
setup.
"""

from __future__ import annotations

import os

from shared_utils import API_KEY_HEADER

# --- AWS resource configuration ------------------------------------------------
#
# Same `AWS_ENDPOINT_URL` convention as crawler/parser-service: set
# locally to point boto3 at LocalStack, unset in real Lambda so boto3
# resolves the real regional endpoints.

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


def boto3_client_kwargs() -> dict:
    """Common kwargs for every boto3 client this service creates."""
    kwargs: dict = {"region_name": AWS_REGION}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs


# --- Database configuration (read-only) ----------------------------------------
#
# api-service only ever reads `domains` (see api/db.py) -- same
# DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SECRET_ARN convention
# parser-service's `parser/config.py` uses, so both services are
# configured identically by Terraform/local dev.

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "agent_intel")
DB_USER = os.environ.get("DB_USER", "agent_intel")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "agent_intel")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")

# --- Raw crawl data (S3), for GET /v1/domains/{domain}/history ----------------
#
# Must match crawler-service's RAW_DATA_BUCKET_NAME -- api-service is a
# read-only consumer of the same bucket crawler-service writes to (see
# crawler/storage.py's key layout: `{domain}/{iso-timestamp}/{artifact}`).

RAW_DATA_BUCKET_NAME = os.environ.get("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-dev")

# --- History endpoint bounds ---------------------------------------------------
#
# `GET /v1/domains/{domain}/history` always bounds how much it returns
# (see api/history.py's docstring for the full rationale) -- these are
# the default/max values for its `limit` query param.
HISTORY_DEFAULT_LIMIT = int(os.environ.get("HISTORY_DEFAULT_LIMIT", "20"))
HISTORY_MAX_LIMIT = int(os.environ.get("HISTORY_MAX_LIMIT", "100"))

# Safety cap on how many of a domain's historical crawl-timestamp
# "folders" a single history request will ever list out of S3, even
# before applying `limit` -- see api/history.py.
HISTORY_S3_LIST_CAP = int(os.environ.get("HISTORY_S3_LIST_CAP", "1000"))

# --- Rate limiting (free tier, per source IP) ----------------------------------
#
# Phase 3 judgment call: no API-key/tiered rate limiting yet (that's
# Phase 4's job -- see api/ratelimit.py's module docstring). A single
# free-tier default rate applies to every caller, keyed by IP, enforced
# via a small DynamoDB table (one item per IP per rate-limit window).
#
# The plan doesn't specify an exact number; 60 req/min is a reasonable,
# easily-overridable default for a free public lookup API.
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_TABLE_NAME = os.environ.get("RATE_LIMIT_TABLE_NAME", "agent-intel-dev-rate-limit")

# --- API-key auth (Phase 4) ------------------------------------------------
#
# See api/auth.py's module docstring for the full design. Header name
# is a judgment call -- `X-API-Key` is the de-facto convention for
# bearer-style API keys (Stripe, SendGrid, etc. all use variants of
# it), and is re-exported from `shared_utils.api_keys` so
# billing-service's key-retrieval response can tell customers the exact
# header name to use without api-service and billing-service risking
# disagreeing on it.

# --- Bulk lookup endpoint (Phase 4) ----------------------------------------
#
# `POST /v1/domains/bulk` caps how many domains one request may ask
# for. The build plan says "cap the list length sensibly (e.g. 100 per
# request)" -- 100 is used as-is: large enough to be genuinely useful
# for a paid bulk-lookup tier, small enough that one request can never
# turn into an unbounded number of `domains` lookups or DynamoDB rate
# writes.
BULK_MAX_DOMAINS = int(os.environ.get("BULK_MAX_DOMAINS", "100"))

# Plan tiers allowed to call the bulk endpoint. `free` is deliberately
# excluded: free tier is the unauthenticated, per-IP-limited path (see
# api/ratelimit.py) -- a free-tier API key existing at all would be a
# contradiction of that design, so this list, not a "tier != free"
# check, is the source of truth for "which tiers may hold a key that
# calls paid endpoints."
BULK_ALLOWED_PLAN_TIERS = ("self_serve", "licensing")

# --- Monitor registration (Phase 5) -----------------------------------------
#
# Monitoring (webhook notifications on domain change) is a paid
# feature, same tier gate as bulk lookup -- reusing the identical set
# rather than inventing a second tuple that could silently drift from
# BULK_ALLOWED_PLAN_TIERS.
MONITOR_ALLOWED_PLAN_TIERS = BULK_ALLOWED_PLAN_TIERS

# --- On-demand crawl triggering (cache-miss path) ---------------------------
#
# `GET /v1/domains/{domain}` used to return a clean 404 on a cache
# miss. It now triggers a real crawl instead -- see
# `api/crawl_trigger.py` and `api/routes.py`'s `_trigger_on_demand_crawl`.
#
# api-service had no SQS send access before this feature (it only ever
# read Postgres/S3/DynamoDB) -- CRAWL_QUEUE_URL is new, mirroring
# scheduler-service's own `CRAWL_QUEUE_URL` env var (same queue, same
# message shape, see scheduler/messaging.py) so this becomes a second/
# third producer onto the SAME queue crawler-service already consumes,
# not a new pipeline entry point.
CRAWL_QUEUE_URL = os.environ.get("CRAWL_QUEUE_URL", "")

# How long one domain's "a crawl is already in flight" marker lives in
# the rate-limit DynamoDB table (see `api/crawl_trigger.py`'s
# `try_acquire_crawl_lock`) before a subsequent lookup is allowed to
# trigger another crawl for the same domain. Long enough to comfortably
# cover a realistic crawl+parse round trip (crawler-service's three
# well-known-path fetches now run in parallel -- see
# services/crawler-service/crawler/fetch.py -- so worst case is roughly
# one fetch timeout, plus S3 writes, SQS hops, and parser-service's own
# upsert), short enough that a genuinely stuck/failed crawl doesn't
# block a domain from ever being retried.
PENDING_CRAWL_TTL_SECONDS = int(os.environ.get("PENDING_CRAWL_TTL_SECONDS", "45"))

# Suggested poll interval (seconds) returned to the caller in the 202
# "crawl triggered" response body -- purely advisory (this service
# doesn't enforce it), consumed by the frontend's polling loop (see
# apps/frontend/app/page.tsx).
CRAWL_POLL_INTERVAL_SECONDS = int(os.environ.get("CRAWL_POLL_INTERVAL_SECONDS", "2"))

# A SEPARATE, tighter per-IP limit specifically on *actually triggering
# a new crawl* (i.e. only counted against a request that wins
# `try_acquire_crawl_lock`'s race -- repeatedly polling an
# already-pending domain never touches this counter, see
# `_trigger_on_demand_crawl` in api/routes.py). This exists because the
# general per-IP lookup limit (`RATE_LIMIT_PER_MINUTE`) was never sized
# with "each request can cause a real outbound HTTP crawl" in mind --
# an attacker could stay comfortably under that general limit while
# still enumerating many distinct never-seen domains to force a lot of
# real crawls, since a *lookup* and a *crawl-triggering lookup* were
# priced the same. A much smaller allowance over a longer window
# reflects that triggering new crawls is the more expensive action.
CRAWL_TRIGGER_RATE_LIMIT = int(os.environ.get("CRAWL_TRIGGER_RATE_LIMIT", "5"))
CRAWL_TRIGGER_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("CRAWL_TRIGGER_RATE_LIMIT_WINDOW_SECONDS", "600"))

# --- Licensing-only bulk export (Phase 5) -----------------------------------
#
# Only `licensing`-tier keys may dump the full `domains` table --
# stricter than the bulk lookup / monitor tiers above, since this is a
# full-dataset export, not a bounded per-request lookup.
EXPORT_ALLOWED_PLAN_TIERS = ("licensing",)

# Where export dumps are written. Deliberately a SEPARATE bucket from
# RAW_DATA_BUCKET_NAME (crawler-service's raw crawl artifacts) rather
# than an `exports/` prefix in that same bucket -- see envs/dev/main.tf's
# comment on `export_bucket` for the full IAM-least-privilege rationale:
# api-service's Lambda role only ever needed READ access to the raw
# crawl bucket before this phase; adding a WRITE path to that same
# bucket (even prefix-scoped) would broaden an existing grant instead of
# adding a new, narrowly-scoped one.
EXPORT_BUCKET_NAME = os.environ.get("EXPORT_BUCKET_NAME", "agent-intel-exports-dev")

# How long a presigned export download URL remains valid. 15 minutes is
# comfortably enough time for a customer to start the download without
# leaving the link usable indefinitely.
EXPORT_PRESIGNED_URL_EXPIRY_SECONDS = int(os.environ.get("EXPORT_PRESIGNED_URL_EXPIRY_SECONDS", "900"))

__all__ = [
    "API_KEY_HEADER",
    "AWS_REGION",
    "BULK_ALLOWED_PLAN_TIERS",
    "BULK_MAX_DOMAINS",
    "CRAWL_POLL_INTERVAL_SECONDS",
    "CRAWL_QUEUE_URL",
    "CRAWL_TRIGGER_RATE_LIMIT",
    "CRAWL_TRIGGER_RATE_LIMIT_WINDOW_SECONDS",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_SECRET_ARN",
    "DB_USER",
    "EXPORT_ALLOWED_PLAN_TIERS",
    "EXPORT_BUCKET_NAME",
    "EXPORT_PRESIGNED_URL_EXPIRY_SECONDS",
    "HISTORY_DEFAULT_LIMIT",
    "HISTORY_MAX_LIMIT",
    "HISTORY_S3_LIST_CAP",
    "MONITOR_ALLOWED_PLAN_TIERS",
    "PENDING_CRAWL_TTL_SECONDS",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_TABLE_NAME",
    "RATE_LIMIT_WINDOW_SECONDS",
    "RAW_DATA_BUCKET_NAME",
    "boto3_client_kwargs",
]
