"""Configuration constants for notifier-service.

Mirrors parser-service's/api-service's `config.py` in style: centralize
env var names in one place, default to values that make local
dev/testing work without extra setup.
"""

from __future__ import annotations

import os

# --- AWS resource configuration ------------------------------------------------

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

# The queue this Lambda is triggered from (only read here for parity
# with the rest of this codebase's `config.py` modules / local scripts;
# the actual SQS event source mapping is configured in Terraform, not
# by this service polling the queue itself).
NOTIFY_QUEUE_URL = os.environ.get("NOTIFY_QUEUE_URL", "")


def boto3_client_kwargs() -> dict:
    """Common kwargs for every boto3 client this service creates."""
    kwargs: dict = {"region_name": AWS_REGION}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs


# --- Database configuration (read-only) ----------------------------------------
#
# notifier-service only ever reads `monitors` (see notifier/db.py) --
# same DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SECRET_ARN
# convention every other DB-touching service in this repo uses.

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "agent_intel")
DB_USER = os.environ.get("DB_USER", "agent_intel")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "agent_intel")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")

# --- Webhook delivery behavior --------------------------------------------------
#
# This Lambda runs inside a single invocation with its own timeout
# (Terraform sets it, see envs/dev/main.tf) -- a small, bounded number
# of retries with short backoff is preferred over anything that could
# approach that timeout. SQS's own redrive/DLQ (notify-queue's DLQ,
# matching crawl-queue's established Phase 1 pattern) is the outer
# safety net for a webhook that fails ALL of these attempts: the
# message becomes visible again after the queue's visibility timeout
# and is retried by a future invocation, eventually landing in the DLQ
# after enough failed deliveries across enough redrives.
WEBHOOK_TIMEOUT_SECONDS = float(os.environ.get("NOTIFIER_WEBHOOK_TIMEOUT_SECONDS", "5"))
WEBHOOK_MAX_ATTEMPTS = int(os.environ.get("NOTIFIER_WEBHOOK_MAX_ATTEMPTS", "3"))
# One backoff delay per retry (len == WEBHOOK_MAX_ATTEMPTS - 1) -- kept
# short (seconds, not minutes) since this all has to fit inside one
# Lambda invocation alongside every other monitor for the same event.
WEBHOOK_BACKOFF_SECONDS = [
    float(s) for s in os.environ.get("NOTIFIER_WEBHOOK_BACKOFF_SECONDS", "1,2").split(",") if s.strip()
]

__all__ = [
    "AWS_REGION",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_SECRET_ARN",
    "DB_USER",
    "NOTIFY_QUEUE_URL",
    "WEBHOOK_BACKOFF_SECONDS",
    "WEBHOOK_MAX_ATTEMPTS",
    "WEBHOOK_TIMEOUT_SECONDS",
    "boto3_client_kwargs",
]
