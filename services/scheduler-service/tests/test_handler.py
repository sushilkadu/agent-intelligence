"""End-to-end (within-process) tests of scheduler-service's Lambda
handler: a faked DB layer (mirrors parser/notifier-service's DB-faking
test style) supplies domain/tier/last_crawled_at data, and enqueueing
onto crawl-queue is exercised against a real moto-mocked SQS -- never
real AWS.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import boto3
from moto import mock_aws

from scheduler import handler

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
QUEUE_NAME = "crawl-queue-test"


class _FakeConnection:
    def close(self) -> None:
        pass


def _row(domain: str, *, tiers: set, last_crawled_at) -> dict:
    return {"domain": domain, "tiers": tiers, "last_crawled_at": last_crawled_at}


@mock_aws
def test_lambda_handler_enqueues_only_due_domains(monkeypatch):
    sqs_client = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs_client.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
    monkeypatch.setattr(handler, "CRAWL_QUEUE_URL", queue_url)

    rows = [
        # Licensing tier, last crawled exactly 1 day ago -> due.
        _row("licensing-due.example", tiers={"licensing"}, last_crawled_at=NOW - timedelta(days=1)),
        # Licensing tier, last crawled recently -> not due.
        _row("licensing-fresh.example", tiers={"licensing"}, last_crawled_at=NOW - timedelta(hours=2)),
        # No monitors, last crawled 8 days ago -> due (weekly default).
        _row("no-monitor-due.example", tiers=set(), last_crawled_at=NOW - timedelta(days=8)),
        # No monitors, last crawled 1 day ago -> not due.
        _row("no-monitor-fresh.example", tiers=set(), last_crawled_at=NOW - timedelta(days=1)),
        # Never crawled at all -> always due.
        _row("never-crawled.example", tiers=set(), last_crawled_at=None),
    ]
    monkeypatch.setattr(handler, "fetch_domains_with_watcher_tiers", lambda _conn: rows)
    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())

    result = handler.lambda_handler({}, None, now=NOW)

    assert result["checked"] == 5
    assert set(result["domains"]) == {"licensing-due.example", "no-monitor-due.example", "never-crawled.example"}
    assert result["enqueued"] == 3

    received = sqs_client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
    enqueued_domains = {json.loads(m["Body"])["domain"] for m in received["Messages"]}
    assert enqueued_domains == {"licensing-due.example", "no-monitor-due.example", "never-crawled.example"}


@mock_aws
def test_lambda_handler_with_no_domains_is_a_noop(monkeypatch):
    sqs_client = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs_client.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
    monkeypatch.setattr(handler, "CRAWL_QUEUE_URL", queue_url)
    monkeypatch.setattr(handler, "fetch_domains_with_watcher_tiers", lambda _conn: [])
    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())

    result = handler.lambda_handler({}, None, now=NOW)

    assert result == {"checked": 0, "enqueued": 0, "domains": []}


@mock_aws
def test_lambda_handler_one_domains_enqueue_failure_does_not_block_the_rest(monkeypatch):
    sqs_client = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs_client.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
    monkeypatch.setattr(handler, "CRAWL_QUEUE_URL", queue_url)

    rows = [
        _row("boom.example", tiers=set(), last_crawled_at=None),
        _row("fine.example", tiers=set(), last_crawled_at=None),
    ]
    monkeypatch.setattr(handler, "fetch_domains_with_watcher_tiers", lambda _conn: rows)
    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())

    def _fake_enqueue(_sqs_client, _queue_url, domain):
        if domain == "boom.example":
            raise RuntimeError("simulated SQS failure")
        return "fake-message-id"

    monkeypatch.setattr(handler, "enqueue_domain_for_recrawl", _fake_enqueue)

    result = handler.lambda_handler({}, None, now=NOW)

    assert result == {"checked": 2, "enqueued": 1, "domains": ["fine.example"]}
