"""S3 read side of raw crawl artifacts (the write side lives in
crawler-service/crawler/storage.py -- this module reads the same
envelope shape back).
"""

from __future__ import annotations

import base64
import json

import boto3

from .config import boto3_client_kwargs


def get_s3_client():
    return boto3.client("s3", **boto3_client_kwargs())


def read_envelope(s3_client, bucket: str, key: str) -> dict:
    """Fetch and JSON-decode the fetch-result envelope at `bucket`/`key`.

    Raises whatever boto3/json raise on a missing/corrupt object --
    callers decide whether that's fatal (it shouldn't be for a single
    domain in a batch; see handler.py's per-record error handling).
    """
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    return json.loads(body)


def decode_envelope_body(envelope: dict) -> bytes:
    """Decode the envelope's base64 `body_base64` back to raw bytes."""
    return base64.b64decode(envelope["body_base64"])


__all__ = ["decode_envelope_body", "get_s3_client", "read_envelope"]
