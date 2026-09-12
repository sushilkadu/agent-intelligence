"""GET /v1/domains/{domain}: found vs. cache-miss (which now triggers a
real on-demand crawl and returns 202, never a dead-end 404 -- see
api/routes.py's `_trigger_on_demand_crawl`). The DB layer is faked
out (mirrors parser-service's approach of faking
`fetch_domain`/`upsert_domain` in handler-level tests, see
parser-service/tests/fakes.py) so this suite never touches real
Postgres -- that's proven separately by the localstack/local-Postgres
verification run.

The cache-miss path DOES touch real (moto-mocked) DynamoDB/SQS --
duplicate-crawl suppression and the crawl-queue enqueue are exactly
what these tests are proving, so they can't be faked out the way the
DB layer is. Same moto-based approach tests/test_ratelimit.py and
tests/test_bulk.py already use.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import boto3
from moto import mock_aws

from api import routes

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

RATE_LIMIT_TABLE = "agent-intel-domains-test"

# A real, public-resolving address stood in for DNS -- see
# test_monitors.py's identical technique/comment for
# validate_webhook_url: a resolver stand-in is used instead of relying
# on real DNS/network access in a unit test.
SAFE_RESOLVED_IP = "93.184.216.34"


def _sample_row(domain: str) -> dict:
    return {
        "domain": domain,
        "first_seen_at": NOW,
        "last_crawled_at": NOW,
        "agent_json_present": True,
        "agent_json_s3_key": f"{domain}/2026-09-09T12:00:00+00:00/agents.json",
        "llms_txt_present": False,
        "llms_txt_s3_key": None,
        "web_bot_auth_present": True,
        "web_bot_auth_key_id": "key-1",
        "web_bot_auth_valid": True,
        "web_bot_auth_expiry": NOW,
        "declared_capabilities": {"agents": [{"name": "demo-bot"}]},
        "confidence_flags": [],
        "on_chain_ref": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _create_rate_limit_table(dynamodb_client) -> None:
    dynamodb_client.create_table(
        TableName=RATE_LIMIT_TABLE,
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _mock_safe_dns(monkeypatch) -> None:
    import shared_utils.webhook_safety as safety_module

    monkeypatch.setattr(
        safety_module.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (SAFE_RESOLVED_IP, 0))]
    )


def test_domain_found_returns_normalized_record(client_no_rate_limit, monkeypatch):
    row = _sample_row("example.com")
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, domain: row if domain == "example.com" else None)

    response = client_no_rate_limit.get("/v1/domains/example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "example.com"
    assert body["agent_json_present"] is True
    assert body["web_bot_auth_valid"] is True
    assert body["confidence_flags"] == []
    assert body["declared_capabilities"] == {"agents": [{"name": "demo-bot"}]}


# --- cache miss: on-demand crawl triggering ----------------------------------


@mock_aws
def test_domain_not_found_triggers_exactly_one_crawl_and_returns_202(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    sqs = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs.create_queue(QueueName="crawl-queue-test")["QueueUrl"]
    monkeypatch.setattr(routes, "CRAWL_QUEUE_URL", queue_url)

    _mock_safe_dns(monkeypatch)

    response = client_no_rate_limit.get("/v1/domains/newly-searched.example")

    assert response.status_code == 202
    body = response.json()
    assert body["domain"] == "newly-searched.example"
    assert body["status"] == "pending"
    assert "retry_after_seconds" in body

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
    assert len(messages) == 1
    assert json.loads(messages[0]["Body"]) == {"domain": "newly-searched.example"}


@mock_aws
def test_second_rapid_lookup_for_the_same_pending_domain_does_not_enqueue_again(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    sqs = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs.create_queue(QueueName="crawl-queue-test")["QueueUrl"]
    monkeypatch.setattr(routes, "CRAWL_QUEUE_URL", queue_url)

    _mock_safe_dns(monkeypatch)

    first = client_no_rate_limit.get("/v1/domains/two-tabs.example")
    second = client_no_rate_limit.get("/v1/domains/two-tabs.example")

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["status"] == "pending"

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
    assert len(messages) == 1


@mock_aws
def test_a_different_pending_domain_still_gets_its_own_crawl(client_no_rate_limit, monkeypatch):
    """Duplicate suppression is per-domain -- it must never accidentally
    suppress an unrelated domain's crawl just because SOME crawl is
    pending.
    """
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    sqs = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs.create_queue(QueueName="crawl-queue-test")["QueueUrl"]
    monkeypatch.setattr(routes, "CRAWL_QUEUE_URL", queue_url)

    _mock_safe_dns(monkeypatch)

    assert client_no_rate_limit.get("/v1/domains/first.example").status_code == 202
    assert client_no_rate_limit.get("/v1/domains/second.example").status_code == 202

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
    assert len(messages) == 2
    bodies = {json.loads(m["Body"])["domain"] for m in messages}
    assert bodies == {"first.example", "second.example"}


# --- cache miss: SSRF safety gate ---------------------------------------------


@mock_aws
def test_cloud_metadata_address_as_domain_is_rejected_before_any_crawl_is_triggered(client_no_rate_limit, monkeypatch):
    """The exact attack this feature introduces: anonymous, user-typed
    input directly triggering our own infrastructure's outbound HTTP
    requests. `169.254.169.254` needs no DNS mocking -- resolving a
    bare IP literal is just parsing, not a real network lookup.
    """
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    sqs = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs.create_queue(QueueName="crawl-queue-test")["QueueUrl"]
    monkeypatch.setattr(routes, "CRAWL_QUEUE_URL", queue_url)

    response = client_no_rate_limit.get("/v1/domains/169.254.169.254")

    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_domain"
    # Not confirming/denying WHY via the message -- see
    # _trigger_on_demand_crawl's docstring.
    assert "169.254.169.254" in response.json()["message"]

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
    assert messages == []


@mock_aws
def test_loopback_hostname_as_domain_is_rejected_before_any_crawl_is_triggered(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    sqs = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs.create_queue(QueueName="crawl-queue-test")["QueueUrl"]
    monkeypatch.setattr(routes, "CRAWL_QUEUE_URL", queue_url)

    import shared_utils.webhook_safety as safety_module

    monkeypatch.setattr(safety_module.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 0))])

    response = client_no_rate_limit.get("/v1/domains/localhost")

    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_domain"

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
    assert messages == []


@mock_aws
def test_private_ip_as_domain_is_rejected(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)
    monkeypatch.setattr(routes, "RATE_LIMIT_TABLE_NAME", RATE_LIMIT_TABLE)
    _create_rate_limit_table(boto3.client("dynamodb", region_name="us-east-1"))

    response = client_no_rate_limit.get("/v1/domains/10.0.0.5")

    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_domain"
