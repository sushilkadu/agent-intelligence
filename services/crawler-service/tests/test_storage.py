"""Unit tests for crawler.storage: S3 key layout, envelope shape, and
the actual put_object call (verified against a moto-mocked S3, not
real AWS/LocalStack).
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import boto3
from moto import mock_aws

from crawler.fetch import FetchResult
from crawler.storage import build_s3_key, fetch_result_to_envelope, store_fetch_result

DOMAIN = "example.com"
TIMESTAMP = "2026-09-09T12:00:00+00:00"


def make_fetch_result(**overrides) -> FetchResult:
    defaults = {
        "url": "https://example.com/agents.json",
        "fetched_at": TIMESTAMP,
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": b'{"agents": []}',
        "present": True,
        "error": None,
    }
    defaults.update(overrides)
    return FetchResult(**defaults)


def test_build_s3_key_layout():
    key = build_s3_key(DOMAIN, TIMESTAMP, "agents.json")
    assert key == "example.com/2026-09-09T12:00:00+00:00/agents.json"


def test_fetch_result_to_envelope_round_trips_body_via_base64():
    fr = make_fetch_result(body=b"\x00\x01raw-bytes-not-utf8-safe")
    envelope = fetch_result_to_envelope(fr)

    assert envelope["status_code"] == 200
    assert envelope["present"] is True
    assert envelope["error"] is None
    assert envelope["headers"] == {"content-type": "application/json"}
    assert base64.b64decode(envelope["body_base64"]) == fr.body
    # The envelope itself must be JSON-serializable regardless of body content.
    json.dumps(envelope)


def test_store_fetch_result_calls_put_object_with_expected_key_and_body():
    s3_client = MagicMock()
    fr = make_fetch_result()

    key = store_fetch_result(s3_client, "my-bucket", DOMAIN, TIMESTAMP, "agents.json", fr)

    assert key == "example.com/2026-09-09T12:00:00+00:00/agents.json"
    s3_client.put_object.assert_called_once()
    call_kwargs = s3_client.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "my-bucket"
    assert call_kwargs["Key"] == key
    assert call_kwargs["ContentType"] == "application/json"
    body = json.loads(call_kwargs["Body"])
    assert body["status_code"] == 200
    assert base64.b64decode(body["body_base64"]) == fr.body


@mock_aws
def test_store_fetch_result_against_real_boto3_s3_api_via_moto():
    # Exercises the real boto3 S3 client + put_object contract (bucket
    # must exist, key layout, content retrievable), not just a mock's
    # call args -- moto intercepts the AWS API so this never touches
    # real AWS or LocalStack.
    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket="agent-intel-raw-crawl-test")

    fr = make_fetch_result()
    key = store_fetch_result(
        s3_client, "agent-intel-raw-crawl-test", DOMAIN, TIMESTAMP, "agents.json", fr
    )

    obj = s3_client.get_object(Bucket="agent-intel-raw-crawl-test", Key=key)
    envelope = json.loads(obj["Body"].read())
    assert envelope["status_code"] == 200
    assert base64.b64decode(envelope["body_base64"]) == fr.body
