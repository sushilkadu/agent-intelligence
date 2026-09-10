"""Configuration constants for scheduler-service.

Mirrors the rest of this codebase's `config.py` modules in style:
centralize env var names in one place, default to values that make
local dev/testing work without extra setup.
"""

from __future__ import annotations

import os

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

# Domains due for recrawl are enqueued back onto the SAME crawl-queue
# crawler-service already consumes from (see crawler/config.py) --
# deliberately reusing that one entry point into the crawl pipeline
# rather than inventing a second one.
CRAWL_QUEUE_URL = os.environ.get("CRAWL_QUEUE_URL", "")


def boto3_client_kwargs() -> dict:
    """Common kwargs for every boto3 client this service creates."""
    kwargs: dict = {"region_name": AWS_REGION}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs


# --- Database configuration (read-only) ----------------------------------------

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "agent_intel")
DB_USER = os.environ.get("DB_USER", "agent_intel")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "agent_intel")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")

# --- Tiered recrawl cadence (Phase 5 judgment call) -----------------------------
#
# The build plan's own example is "licensing-tier domains recrawled
# daily, free-tier weekly" -- but domains themselves don't have a tier,
# CUSTOMERS (api_keys rows) do, and a domain can have zero, one, or
# many monitors owned by keys on different tiers. The interpretation
# used here (see scheduler/cadence.py's `cadence_days_for_tiers`):
#
#   * At least one ACTIVE monitor owned by a `licensing`-tier key
#     -> recrawl daily (1 day).
#   * Else, at least one active monitor owned by a `self_serve`-tier
#     key -> a middle cadence (3 days) -- self_serve customers are
#     paying for monitoring but not at licensing's tier, so they get
#     faster-than-default but not the fastest cadence.
#   * Otherwise (no paid-tier monitor watching it at all -- the common
#     case for most crawled domains) -> the default, slowest cadence
#     (7 days / weekly), matching the build plan's own "free-tier
#     weekly" example.
#
# "Highest tier wins" when a domain has monitors on multiple tiers
# (e.g. one self_serve + one licensing monitor both watching the same
# domain) -- a domain that ANY paying licensing customer cares about
# gets the fast cadence, regardless of who else is also watching it at
# a lower tier.
LICENSING_CADENCE_DAYS = int(os.environ.get("SCHEDULER_LICENSING_CADENCE_DAYS", "1"))
SELF_SERVE_CADENCE_DAYS = int(os.environ.get("SCHEDULER_SELF_SERVE_CADENCE_DAYS", "3"))
DEFAULT_CADENCE_DAYS = int(os.environ.get("SCHEDULER_DEFAULT_CADENCE_DAYS", "7"))

__all__ = [
    "AWS_REGION",
    "CRAWL_QUEUE_URL",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_SECRET_ARN",
    "DB_USER",
    "DEFAULT_CADENCE_DAYS",
    "LICENSING_CADENCE_DAYS",
    "SELF_SERVE_CADENCE_DAYS",
    "boto3_client_kwargs",
]
