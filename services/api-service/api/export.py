"""Licensing-only bulk export: dump the full `domains` table to S3 as
newline-delimited JSON (NDJSON, one row per line) and hand the caller
back a short-lived presigned GET URL, rather than returning the whole
dataset inline in the API response.

--- Why S3 + a presigned URL, not an inline JSON body -------------------

Even at this phase's demo scale (~15 seed domains, growing toward the
build plan's 500-1000+ target), a "full table dump" response doesn't
belong in a normal JSON API response body forever, and an API Gateway
(HTTP API) has its own payload size ceiling well below what a real
production-scale dump could reach. Writing to S3 and handing back a
presigned URL is the standard pattern for "here is a file, go fetch
it," and scales down to today's tiny dataset with the exact same code
path a much bigger one would use.

--- Why synchronous, for THIS phase's scale, and what changes at real scale --

This phase's `export_domains` route (see api/routes.py) does the read
+ NDJSON build + S3 upload + presign all inline, inside one request --
acceptable ONLY because api-service's own Lambda has a real timeout
(`api/config.py`/Terraform) and this phase's dataset is small enough
that a synchronous dump comfortably finishes well inside it. This does
NOT hold at real production scale (the build plan's own "500-1000+
domains" range, or beyond, especially once `declared_capabilities`
JSONB blobs are large): holding an API Gateway/Lambda request open for
a large synchronous dump risks hitting the Lambda timeout or API
Gateway's own 29-second integration timeout, with no way for the
caller to know if a timeout meant "the dump failed" or "the dump
finished but the response never made it back." Flagged explicitly in
the Phase 5 report: this needs to become async before real traffic --
e.g. the route enqueues an export job (a new SQS message), a worker
Lambda (subscribed to that queue, mirroring every other
SQS-triggered service in this codebase) does the actual dump, and the
customer is notified (webhook, email, or a polled job-status endpoint)
once the presigned URL is ready, instead of the request blocking on it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .config import EXPORT_BUCKET_NAME, EXPORT_PRESIGNED_URL_EXPIRY_SECONDS, boto3_client_kwargs


def get_export_s3_client():
    return boto3.client("s3", **boto3_client_kwargs())


def build_ndjson_body(rows: list[dict[str, Any]]) -> bytes:
    """Serialize `rows` as newline-delimited JSON -- one `domains` row
    per line, `default=str` so non-JSON-native types (datetimes) don't
    crash the dump.
    """
    lines = (json.dumps(row, default=str) for row in rows)
    return ("\n".join(lines) + "\n" if rows else "").encode("utf-8")


def build_export_key(key_id: str, *, now: datetime | None = None) -> str:
    """Build the S3 key an export is written to, scoped to the
    requesting customer (`key_id`) and timestamped so repeated exports
    by the same key never collide or overwrite each other.
    """
    now = now or datetime.now(timezone.utc)
    return f"exports/{key_id}/{now.strftime('%Y%m%dT%H%M%SZ')}-domains.ndjson"


def write_export_to_s3(s3_client, bucket: str, key: str, rows: list[dict[str, Any]]) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=build_ndjson_body(rows),
        ContentType="application/x-ndjson",
    )


def generate_presigned_export_url(
    s3_client, bucket: str, key: str, *, expires_in: int = EXPORT_PRESIGNED_URL_EXPIRY_SECONDS
) -> str:
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def run_export(s3_client, rows: list[dict[str, Any]], *, key_id: str, now: datetime | None = None) -> dict[str, Any]:
    """End-to-end export: build the NDJSON body, write it to
    `EXPORT_BUCKET_NAME` under a key scoped to `key_id`, and return a
    presigned GET URL for it. Returns the export key, the URL, its
    expiry, and how many rows were included.
    """
    key = build_export_key(key_id, now=now)
    write_export_to_s3(s3_client, EXPORT_BUCKET_NAME, key, rows)
    url = generate_presigned_export_url(s3_client, EXPORT_BUCKET_NAME, key)
    return {
        "export_key": key,
        "url": url,
        "expires_in": EXPORT_PRESIGNED_URL_EXPIRY_SECONDS,
        "domain_count": len(rows),
    }


__all__ = [
    "build_export_key",
    "build_ndjson_body",
    "generate_presigned_export_url",
    "get_export_s3_client",
    "run_export",
    "write_export_to_s3",
]
