"""GET /v1/domains/{domain}/history against a real (moto-mocked) S3
bucket, exercising the real `api/history.py` listing logic end to end
-- not just with a faked-out history function. Covers a domain with
several snapshots, a domain with none, and that `limit` is respected.
"""

from __future__ import annotations

import base64
import json

import boto3
from moto import mock_aws

from api import routes

BUCKET = "agent-intel-raw-crawl-test"


def _envelope(*, present: bool, status_code: int, fetched_at: str) -> dict:
    return {
        "url": "https://example.test/agents.json",
        "fetched_at": fetched_at,
        "status_code": status_code,
        "present": present,
        "error": None,
        "headers": {},
        "body_base64": base64.b64encode(b"{}").decode("ascii"),
    }


def _put_snapshot(s3_client, domain: str, timestamp: str) -> None:
    for artifact in ("agents.json", "web-bot-auth-directory"):
        s3_client.put_object(
            Bucket=BUCKET,
            Key=f"{domain}/{timestamp}/{artifact}",
            Body=json.dumps(_envelope(present=True, status_code=200, fetched_at=timestamp)).encode("utf-8"),
            ContentType="application/json",
        )


@mock_aws
def test_history_returns_snapshots_most_recent_first(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "RAW_DATA_BUCKET_NAME", BUCKET)

    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket=BUCKET)

    domain = "example.com"
    timestamps = [
        "2026-09-01T12:00:00+00:00",
        "2026-09-05T12:00:00+00:00",
        "2026-09-09T12:00:00+00:00",
    ]
    for ts in timestamps:
        _put_snapshot(s3_client, domain, ts)

    response = client_no_rate_limit.get(f"/v1/domains/{domain}/history")

    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == domain
    assert body["count"] == 3
    assert [s["crawled_at"] for s in body["snapshots"]] == list(reversed(timestamps))
    assert body["snapshots"][0]["agents_json"]["present"] is True
    assert body["snapshots"][0]["agents_json"]["status_code"] == 200
    assert body["snapshots"][0]["web_bot_auth"]["present"] is True


@mock_aws
def test_history_respects_limit(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "RAW_DATA_BUCKET_NAME", BUCKET)

    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket=BUCKET)

    domain = "manysnapshots.com"
    timestamps = [f"2026-09-{day:02d}T00:00:00+00:00" for day in range(1, 11)]
    for ts in timestamps:
        _put_snapshot(s3_client, domain, ts)

    response = client_no_rate_limit.get(f"/v1/domains/{domain}/history", params={"limit": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert body["limit"] == 3
    assert [s["crawled_at"] for s in body["snapshots"]] == list(reversed(timestamps))[:3]


@mock_aws
def test_history_empty_for_never_crawled_domain(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "RAW_DATA_BUCKET_NAME", BUCKET)

    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket=BUCKET)

    response = client_no_rate_limit.get("/v1/domains/never-crawled.example/history")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["snapshots"] == []
