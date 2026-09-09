"""End-to-end (within-process) tests of the Lambda handler: a
hand-built raw-fetched-shaped SQS event goes in, S3/SQS are
moto-mocked, and the DB layer is swapped for an in-memory fake (see
tests/fakes.py) so these tests never touch real Postgres --
tests/test_db.py separately proves the real psycopg2 upsert/fetch
against a real local Postgres.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import boto3
from fakes import FakeDomainStore
from moto import mock_aws

from parser import handler
from parser.confidence import EXPIRED_KEY, MALFORMED_MANIFEST, NO_SIGNALS

BUCKET = "agent-intel-raw-crawl-test"
NOTIFY_QUEUE_NAME = "notify-queue-test"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _envelope(body: bytes | None, *, present: bool, status_code: int | None) -> dict:
    return {
        "url": "https://example.test/thing",
        "fetched_at": NOW.isoformat(),
        "status_code": status_code,
        "present": present,
        "error": None,
        "headers": {},
        "body_base64": base64.b64encode(body or b"").decode("ascii"),
    }


def _put_envelope(s3_client, key: str, body: bytes, *, status_code: int = 200) -> None:
    s3_client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(_envelope(body, present=True, status_code=status_code)).encode("utf-8"),
        ContentType="application/json",
    )


def _raw_fetched_event(
    domain: str,
    *,
    crawled_at: str,
    agents_json_present: bool,
    agents_json_s3_key: str | None,
    web_bot_auth_present: bool,
    web_bot_auth_s3_key: str | None,
) -> dict:
    message = {
        "domain": domain,
        "crawled_at": crawled_at,
        "agents_json": {
            "present": agents_json_present,
            "status_code": 200 if agents_json_present else 404,
            "s3_key": agents_json_s3_key,
        },
        "web_bot_auth": {
            "present": web_bot_auth_present,
            "status_code": 200 if web_bot_auth_present else 404,
            "s3_key": web_bot_auth_s3_key,
        },
    }
    return {"Records": [{"body": json.dumps(message)}]}


class _FakeConnection:
    """Stands in for a psycopg2 connection in handler-level tests --
    fetch_domain/upsert_domain are themselves swapped for the in-memory
    FakeDomainStore, so this object only needs to survive the
    `conn.close()` the handler always calls in its `finally` block.
    """

    def close(self) -> None:
        pass


def _setup(monkeypatch):
    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket=BUCKET)
    sqs_client = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs_client.create_queue(QueueName=NOTIFY_QUEUE_NAME)["QueueUrl"]

    store = FakeDomainStore()

    monkeypatch.setattr(handler, "RAW_DATA_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(handler, "NOTIFY_QUEUE_URL", queue_url)
    monkeypatch.setattr(handler, "get_s3_client", lambda: s3_client)
    monkeypatch.setattr(handler, "get_sqs_client", lambda: sqs_client)
    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(handler, "fetch_domain", store.fetch)
    monkeypatch.setattr(handler, "upsert_domain", store.upsert)

    return s3_client, sqs_client, queue_url, store


def _receive_all(sqs_client, queue_url):
    received = sqs_client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
    return [json.loads(m["Body"]) for m in received.get("Messages", [])]


@mock_aws
def test_valid_manifest_and_valid_key_no_flags(monkeypatch):
    s3_client, sqs_client, queue_url, store = _setup(monkeypatch)

    manifest_body = json.dumps({"agents": [{"name": "demo-bot"}]}).encode("utf-8")
    exp = NOW + timedelta(days=30)
    jwks_body = json.dumps({"keys": [{"kid": "key-1", "exp": int(exp.timestamp())}]}).encode("utf-8")

    _put_envelope(s3_client, "example.com/2026-09-09T12:00:00+00:00/agents.json", manifest_body)
    _put_envelope(s3_client, "example.com/2026-09-09T12:00:00+00:00/web-bot-auth-directory", jwks_body)

    event = _raw_fetched_event(
        "example.com",
        crawled_at=NOW.isoformat(),
        agents_json_present=True,
        agents_json_s3_key="example.com/2026-09-09T12:00:00+00:00/agents.json",
        web_bot_auth_present=True,
        web_bot_auth_s3_key="example.com/2026-09-09T12:00:00+00:00/web-bot-auth-directory",
    )

    result = handler.lambda_handler(event, None, now=NOW)

    assert result == {"processed": 1, "domains": ["example.com"]}
    record = store.rows["example.com"]
    assert record["declared_capabilities"] == {"agents": [{"name": "demo-bot"}]}
    assert record["web_bot_auth_key_id"] == "key-1"
    assert record["web_bot_auth_valid"] is True
    assert record["confidence_flags"] == []
    assert MALFORMED_MANIFEST not in record["confidence_flags"]
    assert EXPIRED_KEY not in record["confidence_flags"]
    # First-ever crawl for this domain: no record-changed published.
    assert _receive_all(sqs_client, queue_url) == []


@mock_aws
def test_expired_key_sets_flag(monkeypatch):
    s3_client, _sqs_client, _queue_url, store = _setup(monkeypatch)

    manifest_body = json.dumps({"agents": []}).encode("utf-8")
    expired = NOW - timedelta(days=1)
    jwks_body = json.dumps({"keys": [{"kid": "key-1", "exp": int(expired.timestamp())}]}).encode("utf-8")

    _put_envelope(s3_client, "expired.example/2026-09-09T12:00:00+00:00/agents.json", manifest_body)
    _put_envelope(s3_client, "expired.example/2026-09-09T12:00:00+00:00/web-bot-auth-directory", jwks_body)

    event = _raw_fetched_event(
        "expired.example",
        crawled_at=NOW.isoformat(),
        agents_json_present=True,
        agents_json_s3_key="expired.example/2026-09-09T12:00:00+00:00/agents.json",
        web_bot_auth_present=True,
        web_bot_auth_s3_key="expired.example/2026-09-09T12:00:00+00:00/web-bot-auth-directory",
    )

    handler.lambda_handler(event, None, now=NOW)

    record = store.rows["expired.example"]
    assert record["confidence_flags"] == [EXPIRED_KEY]
    assert record["web_bot_auth_valid"] is False
    assert record["web_bot_auth_key_id"] == "key-1"
    assert record["declared_capabilities"] == {"agents": []}
    assert MALFORMED_MANIFEST not in record["confidence_flags"]


@mock_aws
def test_malformed_manifest_does_not_crash_and_other_data_still_processed(monkeypatch):
    s3_client, _sqs_client, _queue_url, store = _setup(monkeypatch)

    _put_envelope(s3_client, "bad.example/2026-09-09T12:00:00+00:00/agents.json", b"{not valid json")
    exp = NOW + timedelta(days=30)
    jwks_body = json.dumps({"keys": [{"kid": "key-1", "exp": int(exp.timestamp())}]}).encode("utf-8")
    _put_envelope(s3_client, "bad.example/2026-09-09T12:00:00+00:00/web-bot-auth-directory", jwks_body)

    event = _raw_fetched_event(
        "bad.example",
        crawled_at=NOW.isoformat(),
        agents_json_present=True,
        agents_json_s3_key="bad.example/2026-09-09T12:00:00+00:00/agents.json",
        web_bot_auth_present=True,
        web_bot_auth_s3_key="bad.example/2026-09-09T12:00:00+00:00/web-bot-auth-directory",
    )

    result = handler.lambda_handler(event, None, now=NOW)

    assert result == {"processed": 1, "domains": ["bad.example"]}
    record = store.rows["bad.example"]
    assert record["confidence_flags"] == [MALFORMED_MANIFEST]
    assert record["declared_capabilities"] == {}
    # The Web Bot Auth signal was valid and still processed correctly
    # even though agents.json was malformed.
    assert record["web_bot_auth_key_id"] == "key-1"
    assert record["web_bot_auth_valid"] is True


@mock_aws
def test_neither_signal_present_is_no_signals(monkeypatch):
    _setup(monkeypatch)

    event = _raw_fetched_event(
        "absent.example",
        crawled_at=NOW.isoformat(),
        agents_json_present=False,
        agents_json_s3_key=None,
        web_bot_auth_present=False,
        web_bot_auth_s3_key=None,
    )

    result = handler.lambda_handler(event, None, now=NOW)

    assert result == {"processed": 1, "domains": ["absent.example"]}


@mock_aws
def test_no_signals_flag_and_sensible_record(monkeypatch):
    _s3_client, _sqs_client, _queue_url, store = _setup(monkeypatch)

    event = _raw_fetched_event(
        "absent.example",
        crawled_at=NOW.isoformat(),
        agents_json_present=False,
        agents_json_s3_key=None,
        web_bot_auth_present=False,
        web_bot_auth_s3_key=None,
    )

    handler.lambda_handler(event, None, now=NOW)

    record = store.rows["absent.example"]
    assert record["confidence_flags"] == [NO_SIGNALS]
    assert record["agent_json_present"] is False
    assert record["web_bot_auth_present"] is False
    assert record["declared_capabilities"] == {}
    assert record["web_bot_auth_key_id"] is None
    assert record["web_bot_auth_valid"] is False


@mock_aws
def test_record_changed_published_when_a_field_changes(monkeypatch):
    s3_client, sqs_client, queue_url, _store = _setup(monkeypatch)

    # Far in the future relative to `now=NOW` passed below -- valid.
    future_exp = int((NOW + timedelta(days=30)).timestamp())
    # Far in the past relative to `now=NOW` -- expired. Using
    # unambiguous absolute offsets from the fixed `NOW` (rather than
    # depending on the real wall clock) keeps this test deterministic;
    # `now=NOW` is passed explicitly to lambda_handler below for the
    # same reason.
    past_exp = int((NOW - timedelta(days=1)).timestamp())

    key = "changing.example/2026-09-09T12:00:00+00:00/web-bot-auth-directory"
    _put_envelope(s3_client, key, json.dumps({"keys": [{"kid": "key-1", "exp": future_exp}]}).encode())

    first_event = _raw_fetched_event(
        "changing.example",
        crawled_at=NOW.isoformat(),
        agents_json_present=False,
        agents_json_s3_key=None,
        web_bot_auth_present=True,
        web_bot_auth_s3_key=key,
    )
    handler.lambda_handler(first_event, None, now=NOW)
    # First-ever crawl: nothing published.
    assert _receive_all(sqs_client, queue_url) == []

    # Second crawl: the same key id now reports an expired `exp` --
    # web_bot_auth_valid flips from True to False, and confidence_flags
    # gains "expired_key".
    later = NOW + timedelta(days=60)
    key2 = "changing.example/2026-09-09T13:00:00+00:00/web-bot-auth-directory"
    _put_envelope(s3_client, key2, json.dumps({"keys": [{"kid": "key-1", "exp": past_exp}]}).encode())
    second_event = _raw_fetched_event(
        "changing.example",
        crawled_at=later.isoformat(),
        agents_json_present=False,
        agents_json_s3_key=None,
        web_bot_auth_present=True,
        web_bot_auth_s3_key=key2,
    )
    handler.lambda_handler(second_event, None, now=later)

    messages = _receive_all(sqs_client, queue_url)
    assert len(messages) == 1
    assert messages[0]["event"] == "record-changed"
    assert messages[0]["domain"] == "changing.example"
    assert "web_bot_auth_valid" in messages[0]["changed_fields"]
    assert messages[0]["changed_fields"]["web_bot_auth_valid"] == {"old": True, "new": False}
    assert "confidence_flags" in messages[0]["changed_fields"]
    # The S3 key changed too (a new timestamped object every crawl) but
    # that's a storage pointer, not a signal -- must not appear in the
    # diff (see normalize.py's DIFF_FIELDS).
    assert "agent_json_s3_key" not in messages[0]["changed_fields"]


@mock_aws
def test_no_record_changed_published_when_nothing_changes(monkeypatch):
    s3_client, sqs_client, queue_url, _store = _setup(monkeypatch)

    def make_event(crawled_at: str, s3_key: str) -> dict:
        return _raw_fetched_event(
            "stable.example",
            crawled_at=crawled_at,
            agents_json_present=True,
            agents_json_s3_key=s3_key,
            web_bot_auth_present=False,
            web_bot_auth_s3_key=None,
        )

    key = "stable.example/2026-09-09T12:00:00+00:00/agents.json"
    _put_envelope(s3_client, key, json.dumps({"agents": []}).encode())
    handler.lambda_handler(make_event(NOW.isoformat(), key), None, now=NOW)
    assert _receive_all(sqs_client, queue_url) == []

    # Second crawl: identical agents.json content written to a *new*
    # timestamped S3 key (exactly what crawler-service does on every
    # real crawl, see crawler/storage.py) -- since agent_json_s3_key is
    # deliberately excluded from the diff (normalize.py's DIFF_FIELDS)
    # and the parsed content (declared_capabilities) is unchanged,
    # nothing should be published.
    key2 = "stable.example/2026-09-09T13:00:00+00:00/agents.json"
    _put_envelope(s3_client, key2, json.dumps({"agents": []}).encode())
    later = NOW + timedelta(hours=1)
    handler.lambda_handler(make_event(later.isoformat(), key2), None, now=later)

    assert _receive_all(sqs_client, queue_url) == []


@mock_aws
def test_lambda_handler_with_no_records_is_a_noop(monkeypatch):
    _setup(monkeypatch)

    result = handler.lambda_handler({"Records": []}, None)

    assert result == {"processed": 0, "domains": []}


def test_extract_message_from_valid_body():
    record = {"body": json.dumps({"domain": "example.com", "crawled_at": "x"})}
    message = handler.extract_message(record)
    assert message == {"domain": "example.com", "crawled_at": "x"}


def test_extract_message_returns_none_for_invalid_body():
    assert handler.extract_message({"body": "not json"}) is None
    assert handler.extract_message({"body": ""}) is None
    assert handler.extract_message({}) is None
    assert handler.extract_message({"body": json.dumps({"no_domain": True})}) is None
