"""`POST /v1/export`: licensing-tier-only bulk export of the full
`domains` table to S3 as NDJSON, returning a presigned GET URL.

Tier gating mirrors `tests/test_bulk.py`'s style; DB access is faked
(`fetch_all_domains`); S3 is real via moto so the presigned URL and
uploaded object are exercised against the real boto3 S3 API contract,
not just mock call args.
"""

from __future__ import annotations

import json

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from api import auth, routes
from api.config import EXPORT_BUCKET_NAME
from app import app


class _DummyConn:
    def close(self) -> None:
        pass


def _key_row(key_id: str = "key-1", plan_tier: str = "licensing", rate_limit: int = 1000, active: bool = True) -> dict:
    return {"key_id": key_id, "plan_tier": plan_tier, "rate_limit": rate_limit, "active": active}


def test_export_with_no_api_key_is_a_clean_403(monkeypatch):
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())

    client = TestClient(app)
    response = client.post("/v1/export")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@mock_aws
def test_export_with_a_self_serve_key_is_a_clean_403_not_500(monkeypatch):
    """Export is stricter than bulk lookup -- self_serve is enough for
    bulk domain lookup but NOT enough for a full-table export.
    """
    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row(plan_tier="self_serve"))

    client = TestClient(app)
    response = client.post("/v1/export", headers={"X-API-Key": "ai_live_selfserve"})

    assert response.status_code == 403


@mock_aws
def test_export_with_a_licensing_key_returns_a_real_presigned_url(monkeypatch):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=EXPORT_BUCKET_NAME)

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row(key_id="key-licensing"))
    monkeypatch.setattr(
        routes,
        "fetch_all_domains",
        lambda _conn: [
            {"domain": "example.com", "agent_json_present": True},
            {"domain": "other.example", "agent_json_present": False},
        ],
    )

    client = TestClient(app)
    response = client.post("/v1/export", headers={"X-API-Key": "ai_live_licensing"})

    assert response.status_code == 200
    body = response.json()
    assert body["domain_count"] == 2
    assert body["expires_in"] == 900
    assert body["export_key"].startswith("exports/key-licensing/")
    assert body["export_key"].endswith("-domains.ndjson")
    # A real presigned URL shape: signed query params present, pointing
    # at the export bucket/key.
    assert body["url"].startswith("http")
    assert EXPORT_BUCKET_NAME in body["url"] or body["export_key"] in body["url"]
    assert any(token in body["url"] for token in ("Signature", "X-Amz-Signature"))

    # Confirm the object's actual NDJSON contents were written to S3
    # (fetching the presigned URL itself via a real HTTP client, not
    # through botocore, is exercised separately against real LocalStack
    # in scripts/verify_localstack_e2e.py -- moto's HTTP interception
    # doesn't reliably cover a raw `urllib`/`requests` GET the way it
    # covers botocore calls).
    s3_client = boto3.client("s3", region_name="us-east-1")
    obj = s3_client.get_object(Bucket=EXPORT_BUCKET_NAME, Key=body["export_key"])
    content = obj["Body"].read().decode("utf-8")
    lines = [json.loads(line) for line in content.strip().splitlines()]
    assert {row["domain"] for row in lines} == {"example.com", "other.example"}


@mock_aws
def test_export_with_no_domains_returns_an_empty_but_valid_export(monkeypatch):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=EXPORT_BUCKET_NAME)

    monkeypatch.setattr(routes, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _h: _key_row())
    monkeypatch.setattr(routes, "fetch_all_domains", lambda _conn: [])

    client = TestClient(app)
    response = client.post("/v1/export", headers={"X-API-Key": "ai_live_licensing"})

    assert response.status_code == 200
    assert response.json()["domain_count"] == 0
