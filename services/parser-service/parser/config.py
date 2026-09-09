"""Configuration constants for parser-service.

Mirrors crawler-service's crawler/config.py in style: centralize env
var names in one place, default to values that make local dev/testing
work without extra setup.
"""

from __future__ import annotations

import os

# --- AWS resource configuration ------------------------------------------------
#
# Same `AWS_ENDPOINT_URL` convention as crawler-service: set locally to
# point boto3 at LocalStack, unset in real Lambda so boto3 resolves the
# real regional endpoints.

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

# Must match crawler-service's RAW_DATA_BUCKET_NAME -- the raw-fetched
# message only carries S3 *keys*, not the bucket name (see
# crawler/messaging.py), so both services are configured with the same
# bucket name independently rather than one telling the other.
RAW_DATA_BUCKET_NAME = os.environ.get("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-dev")

# The raw-fetched queue this Lambda is triggered from (only read here
# for parity with crawler-service's config.py / local scripts; the
# actual SQS event source mapping is configured in Terraform, not by
# this service polling the queue itself).
RAW_FETCHED_QUEUE_URL = os.environ.get("RAW_FETCHED_QUEUE_URL", "")

# Published to once a domain's normalized record changes from its
# previous crawl. Consumed by notifier-service (Phase 5, not built
# here).
NOTIFY_QUEUE_URL = os.environ.get("NOTIFY_QUEUE_URL", "")


def boto3_client_kwargs() -> dict:
    """Common kwargs for every boto3 client this service creates."""
    kwargs: dict = {"region_name": AWS_REGION}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs


# --- Database configuration -----------------------------------------------
#
# Local dev / docker-compose: DB_PASSWORD is read directly from the
# environment (matching docker-compose.yml's plaintext POSTGRES_PASSWORD
# -- there's no secret store running locally). Real deployment: DB_SECRET_ARN
# names a Secrets Manager secret (populated by Terraform's `rds` module,
# which uses RDS's native `manage_master_user_password` so the password
# is never handled by Terraform state or a human -- see
# infra/terraform/modules/rds/main.tf) holding a
# `{"username": ..., "password": ...}` JSON payload, per AWS's
# documented shape for RDS-managed master-user secrets. When both are
# set, DB_SECRET_ARN wins.

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "agent_intel")
DB_USER = os.environ.get("DB_USER", "agent_intel")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "agent_intel")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")

__all__ = [
    "AWS_REGION",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_SECRET_ARN",
    "DB_USER",
    "NOTIFY_QUEUE_URL",
    "RAW_DATA_BUCKET_NAME",
    "RAW_FETCHED_QUEUE_URL",
    "boto3_client_kwargs",
]
