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

__all__ = [
    "API_KEY_HEADER",
    "AWS_REGION",
    "BULK_ALLOWED_PLAN_TIERS",
    "BULK_MAX_DOMAINS",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_SECRET_ARN",
    "DB_USER",
    "HISTORY_DEFAULT_LIMIT",
    "HISTORY_MAX_LIMIT",
    "HISTORY_S3_LIST_CAP",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_TABLE_NAME",
    "RATE_LIMIT_WINDOW_SECONDS",
    "RAW_DATA_BUCKET_NAME",
    "boto3_client_kwargs",
]
