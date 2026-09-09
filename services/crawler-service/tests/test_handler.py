"""End-to-end (within-process) test of the Lambda handler: a
hand-built SQS-event-shaped dict goes in, and we assert S3 objects were
written and a raw-fetched message was published -- all against
moto-mocked AWS and respx-mocked HTTP, never real infrastructure.
"""

from __future__ import annotations

import json

import boto3
import httpx
import respx
from moto import mock_aws

from crawler import handler
from crawler.config import AGENTS_JSON_PATH, WEB_BOT_AUTH_WELL_KNOWN_PATH
from crawler.fetch import build_url
from crawler.handler import extract_domain, lambda_handler

BUCKET = "agent-intel-raw-crawl-test"
QUEUE_NAME = "raw-fetched-test"


def test_extract_domain_from_json_body():
    record = {"body": json.dumps({"domain": "example.com"})}
    assert extract_domain(record) == "example.com"


def test_extract_domain_from_bare_string_body():
    record = {"body": "example.com"}
    assert extract_domain(record) == "example.com"


def test_extract_domain_returns_none_for_empty_body():
    assert extract_domain({"body": ""}) is None
    assert extract_domain({}) is None


def _sqs_event(domains):
    return {"Records": [{"body": json.dumps({"domain": d})} for d in domains]}


@mock_aws
@respx.mock
def test_lambda_handler_end_to_end(monkeypatch):
    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket=BUCKET)
    sqs_client = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs_client.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]

    monkeypatch.setattr(handler, "RAW_DATA_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(handler, "RAW_FETCHED_QUEUE_URL", queue_url)

    domain_a = "has-both-signals.example"
    domain_b = "has-neither-signal.example"

    respx.get(build_url(domain_a, AGENTS_JSON_PATH)).mock(
        return_value=httpx.Response(200, json={"agents": [{"name": "demo"}]})
    )
    respx.get(build_url(domain_a, WEB_BOT_AUTH_WELL_KNOWN_PATH)).mock(
        return_value=httpx.Response(200, json={"keys": []})
    )
    respx.get(build_url(domain_b, AGENTS_JSON_PATH)).mock(return_value=httpx.Response(404))
    respx.get(build_url(domain_b, WEB_BOT_AUTH_WELL_KNOWN_PATH)).mock(
        side_effect=httpx.ConnectError("no route to host")
    )

    result = lambda_handler(_sqs_event([domain_a, domain_b]), None)

    assert result == {"processed": 2, "domains": [domain_a, domain_b]}

    # Both domains wrote both artifacts to S3.
    keys = {obj["Key"] for obj in s3_client.list_objects_v2(Bucket=BUCKET)["Contents"]}
    assert len(keys) == 4
    assert any(k.startswith(domain_a) and k.endswith("agents.json") for k in keys)
    assert any(k.startswith(domain_a) and k.endswith("web-bot-auth-directory") for k in keys)
    assert any(k.startswith(domain_b) and k.endswith("agents.json") for k in keys)
    assert any(k.startswith(domain_b) and k.endswith("web-bot-auth-directory") for k in keys)

    # One raw-fetched message per domain, with the right presence flags.
    received = sqs_client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
    bodies = {json.loads(m["Body"])["domain"]: json.loads(m["Body"]) for m in received["Messages"]}
    assert set(bodies) == {domain_a, domain_b}
    assert bodies[domain_a]["agents_json"]["present"] is True
    assert bodies[domain_a]["web_bot_auth"]["present"] is True
    assert bodies[domain_b]["agents_json"]["present"] is False
    assert bodies[domain_b]["agents_json"]["status_code"] == 404
    assert bodies[domain_b]["web_bot_auth"]["present"] is False
    assert bodies[domain_b]["web_bot_auth"]["status_code"] is None


@mock_aws
def test_lambda_handler_with_no_records_is_a_noop(monkeypatch):
    monkeypatch.setattr(handler, "RAW_DATA_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(handler, "RAW_FETCHED_QUEUE_URL", "unused")

    result = lambda_handler({"Records": []}, None)

    assert result == {"processed": 0, "domains": []}
