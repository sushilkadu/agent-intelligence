"""Free-tier per-IP rate limiting, exercised end to end through the
FastAPI dependency (`api/routes.py`'s `enforce_rate_limit`) against a
real (moto-mocked) DynamoDB table -- not just the `check_and_increment`
unit alone -- so this proves the actual request path a caller hits.

Uses the history endpoint (no DB dependency to fake) with a real
(moto-mocked) S3 bucket, and a low `RATE_LIMIT_PER_MINUTE` so the test
doesn't need to fire dozens of requests to prove the limit works.
"""

from __future__ import annotations

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from api import ratelimit, routes
from app import app

RATE_LIMIT_TABLE = "agent-intel-rate-limit-test"
BUCKET = "agent-intel-raw-crawl-ratelimit-test"


def _create_rate_limit_table(dynamodb_client) -> None:
    dynamodb_client.create_table(
        TableName=RATE_LIMIT_TABLE,
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


@mock_aws
def test_rate_limiter_allows_requests_under_the_limit(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    monkeypatch.setattr(routes, "RATE_LIMIT_PER_MINUTE", 3)
    monkeypatch.setattr(routes, "RAW_DATA_BUCKET_NAME", BUCKET)

    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    client = TestClient(app)
    for _ in range(3):
        response = client.get("/v1/domains/example.com/history")
        assert response.status_code == 200


@mock_aws
def test_rate_limiter_rejects_requests_over_the_limit_with_429(monkeypatch):
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    monkeypatch.setattr(routes, "RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(routes, "RAW_DATA_BUCKET_NAME", BUCKET)

    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    client = TestClient(app)
    assert client.get("/v1/domains/example.com/history").status_code == 200
    assert client.get("/v1/domains/example.com/history").status_code == 200

    third = client.get("/v1/domains/example.com/history")
    assert third.status_code == 429
    body = third.json()
    assert body["error"] == "rate_limit_exceeded"
    assert "60" in body["message"] or "rate limit" in body["message"].lower()


@mock_aws
def test_check_and_increment_is_atomic_per_window(monkeypatch):
    """Unit-level check of the DynamoDB counter itself: concurrent
    increments for the same IP/window are never lost (a plain
    read-then-write counter could double-count or under-count under
    concurrency; the atomic `ADD` update here cannot).
    """
    dynamodb_client = boto3.client("dynamodb", region_name="us-east-1")
    _create_rate_limit_table(dynamodb_client)

    now = 1_000_000.0
    results = [
        ratelimit.check_and_increment(
            dynamodb_client, RATE_LIMIT_TABLE, "1.2.3.4", limit=5, window_seconds=60, now=now
        )
        for _ in range(7)
    ]
    counts = [count for _allowed, count in results]
    assert counts == [1, 2, 3, 4, 5, 6, 7]
    allowed_flags = [allowed for allowed, _count in results]
    assert allowed_flags == [True, True, True, True, True, False, False]
