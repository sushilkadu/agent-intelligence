"""S3 storage of raw crawl artifacts.

Each fetched artifact (agents.json, the Web Bot Auth JWKS directory) is
stored as a single JSON envelope object -- status code, headers, any
fetch-level error, and the raw response body (base64-encoded, since
the body isn't guaranteed to be valid JSON/UTF-8 and the envelope
itself must be valid JSON). Keyed by domain + ISO crawl timestamp so
every historical crawl of a domain is retained, e.g.:

    example.com/2026-09-09T12:00:00+00:00/agents.json
    example.com/2026-09-09T12:00:00+00:00/web-bot-auth-directory
"""

from __future__ import annotations

import base64
import json

import boto3

from .config import boto3_client_kwargs
from .fetch import FetchResult


def get_s3_client():
    return boto3.client("s3", **boto3_client_kwargs())


def build_s3_key(domain: str, timestamp: str, artifact_name: str) -> str:
    return f"{domain}/{timestamp}/{artifact_name}"


def fetch_result_to_envelope(fetch_result: FetchResult) -> dict:
    """Convert a `FetchResult` into a JSON-serializable envelope."""
    return {
        "url": fetch_result.url,
        "fetched_at": fetch_result.fetched_at,
        "status_code": fetch_result.status_code,
        "present": fetch_result.present,
        "error": fetch_result.error,
        "headers": fetch_result.headers,
        "body_base64": base64.b64encode(fetch_result.body).decode("ascii"),
    }


def store_fetch_result(
    s3_client,
    bucket: str,
    domain: str,
    timestamp: str,
    artifact_name: str,
    fetch_result: FetchResult,
) -> str:
    """Write `fetch_result`'s envelope to S3, returning the key written."""
    key = build_s3_key(domain, timestamp, artifact_name)
    envelope = fetch_result_to_envelope(fetch_result)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(envelope).encode("utf-8"),
        ContentType="application/json",
    )
    return key


__all__ = [
    "build_s3_key",
    "fetch_result_to_envelope",
    "get_s3_client",
    "store_fetch_result",
]
