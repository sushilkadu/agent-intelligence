"""Unit tests for crawler.messaging: raw-fetched message shape and publish."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import boto3
from moto import mock_aws

from crawler.messaging import build_raw_fetched_message, publish_raw_fetched


def test_build_raw_fetched_message_shape():
    message = build_raw_fetched_message(
        domain="example.com",
        crawled_at="2026-09-09T12:00:00+00:00",
        agents_json_present=True,
        agents_json_status_code=200,
        agents_json_s3_key="example.com/2026-09-09T12:00:00+00:00/agents.json",
        web_bot_auth_present=False,
        web_bot_auth_status_code=404,
        web_bot_auth_s3_key="example.com/2026-09-09T12:00:00+00:00/web-bot-auth-directory",
    )

    assert message["domain"] == "example.com"
    assert message["agents_json"] == {
        "present": True,
        "status_code": 200,
        "s3_key": "example.com/2026-09-09T12:00:00+00:00/agents.json",
    }
    assert message["web_bot_auth"] == {
        "present": False,
        "status_code": 404,
        "s3_key": "example.com/2026-09-09T12:00:00+00:00/web-bot-auth-directory",
    }
    # Must be JSON-serializable as-is (it becomes the SQS MessageBody).
    json.dumps(message)


def test_publish_raw_fetched_calls_send_message():
    sqs_client = MagicMock()
    sqs_client.send_message.return_value = {"MessageId": "abc-123"}

    message_id = publish_raw_fetched(sqs_client, "https://queue.example/raw-fetched", {"domain": "example.com"})

    assert message_id == "abc-123"
    sqs_client.send_message.assert_called_once()
    call_kwargs = sqs_client.send_message.call_args.kwargs
    assert call_kwargs["QueueUrl"] == "https://queue.example/raw-fetched"
    assert json.loads(call_kwargs["MessageBody"]) == {"domain": "example.com"}


@mock_aws
def test_publish_raw_fetched_against_real_boto3_sqs_api_via_moto():
    sqs_client = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs_client.create_queue(QueueName="raw-fetched-test")["QueueUrl"]

    message = build_raw_fetched_message(
        domain="example.com",
        crawled_at="2026-09-09T12:00:00+00:00",
        agents_json_present=True,
        agents_json_status_code=200,
        agents_json_s3_key="k1",
        web_bot_auth_present=True,
        web_bot_auth_status_code=200,
        web_bot_auth_s3_key="k2",
    )
    publish_raw_fetched(sqs_client, queue_url, message)

    received = sqs_client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1)
    body = json.loads(received["Messages"][0]["Body"])
    assert body["domain"] == "example.com"
    assert body["agents_json"]["present"] is True
