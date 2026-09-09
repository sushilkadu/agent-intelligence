#!/usr/bin/env python3
"""End-to-end verification of the FULL Phase 1 + Phase 2 pipeline
against real LocalStack (S3 + SQS) and a real local Postgres, using a
local mock HTTP server instead of the real internet so the run is
deterministic and needs no network egress.

This is the single most important verification for Phase 2: it proves
the chain crawler-service produces really is what parser-service really
consumes, not just that each side's mocks agree with each other.

What this proves, end to end, using the real AWS APIs (via LocalStack)
and a real Postgres (via docker-compose), not mocks:
  1. A domain is seeded onto a real `crawl-queue` SQS queue (same as
     crawler-service's own verify script / scripts/load_seed_list.py).
  2. crawler-service's real `crawler.handler.lambda_handler` crawls it
     against a local mock HTTP server, writes real objects to a real
     S3 bucket, and publishes a real `raw-fetched` message.
  3. That real raw-fetched message is received off a real SQS queue
     and fed directly into parser-service's real
     `parser.handler.lambda_handler`.
  4. The Lambda handler reads the real S3 objects crawler-service just
     wrote, parses/validates them, and upserts a real row into the
     real local Postgres `domains` table (migrated via Alembic).
  5. The row is queried back out of Postgres directly (psycopg2) and
     printed/asserted against, proving the whole chain -- not just
     that parser-service's internal logic is self-consistent.
  6. A second crawl of the same domain with a changed signal produces
     a real `record-changed` message on a real `notify-queue`.

Prerequisites:
    docker compose up -d postgres localstack   # from the repo root
    (packages/shared-schema)$ DATABASE_URL=postgresql+psycopg2://agent_intel:agent_intel@localhost:5432/agent_intel alembic upgrade head

Usage (from services/parser-service, with both this service's and
crawler-service's requirements.txt installed in the same environment):
    python scripts/verify_localstack_e2e.py

Everything here targets LocalStack (http://localhost:4566 by default,
override with LOCALSTACK_ENDPOINT_URL) and local Postgres (localhost:5432
by default, matching docker-compose.yml) -- never real AWS/real RDS.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Configure env vars BEFORE importing anything from `crawler`/`parser` ----
# (both packages' config modules read these once, at import time.)

LOCALSTACK_ENDPOINT_URL = os.environ.get("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
os.environ["AWS_ENDPOINT_URL"] = LOCALSTACK_ENDPOINT_URL
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")
# Point crawl URLs at http://<host:port> (the local mock server) instead
# of https://<domain> -- same override crawler-service's own
# verify_localstack_e2e.py uses.
os.environ["CRAWLER_URL_SCHEME"] = "http"

BUCKET_NAME = os.environ.setdefault("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-verify-p2")
CRAWL_QUEUE_NAME = "crawl-queue-verify-p2"
RAW_FETCHED_QUEUE_NAME = "raw-fetched-verify-p2"
NOTIFY_QUEUE_NAME = "notify-queue-verify-p2"

# Local Postgres, matching docker-compose.yml's defaults unless overridden.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "agent_intel")
os.environ.setdefault("DB_USER", "agent_intel")
os.environ.setdefault("DB_PASSWORD", "agent_intel")

THIS_DIR = Path(__file__).resolve().parent
PARSER_SERVICE_DIR = THIS_DIR.parent
CRAWLER_SERVICE_DIR = PARSER_SERVICE_DIR.parent / "crawler-service"

sys.path.insert(0, str(PARSER_SERVICE_DIR))
sys.path.insert(0, str(CRAWLER_SERVICE_DIR))

import boto3
import psycopg2
import psycopg2.extras
from crawler.config import boto3_client_kwargs as crawler_boto3_client_kwargs
from crawler.handler import lambda_handler as crawler_lambda_handler

from parser.handler import lambda_handler as parser_lambda_handler

# --- A tiny local HTTP server standing in for a real domain ------------------


class _FakeSignalsHandler(http.server.BaseHTTPRequestHandler):
    """Serves a valid agents.json + a Web Bot Auth JWKS directory whose
    key's `exp` is controlled by the module-level `_KEY_EXPIRY_TS`
    global, so the second crawl (below) can flip the key from valid to
    expired without needing a second server.
    """

    def log_message(self, *args):  # silence default request logging
        pass

    def do_GET(self):
        if self.path == "/agents.json":
            body = json.dumps(
                {"agents": [{"name": "verify-bot", "capabilities": ["chat", "search"]}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/.well-known/http-message-signatures-directory":
            body = json.dumps(
                {"keys": [{"kty": "OKP", "kid": "verify-key-1", "exp": _KEY_EXPIRY_TS[0]}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/jwk-set+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


# Mutable via a 1-element list so the running server thread picks up
# the new value when the "second crawl" section below changes it.
_NOW = datetime.now(timezone.utc)
_KEY_EXPIRY_TS = [int((_NOW + timedelta(days=30)).timestamp())]


def _start_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeSignalsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _crawl_and_get_raw_fetched_message(domain, crawl_queue_url, raw_fetched_queue_url, sqs_client):
    """Runs one crawl-queue -> crawler_lambda_handler -> raw-fetched
    cycle for `domain` (exactly like crawler-service's own verify
    script) and returns the resulting raw-fetched message body (a
    dict).
    """
    sqs_client.send_message(QueueUrl=crawl_queue_url, MessageBody=json.dumps({"domain": domain}))
    received = sqs_client.receive_message(QueueUrl=crawl_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    messages = received.get("Messages", [])
    assert len(messages) == 1, f"expected 1 message off crawl-queue, got {len(messages)}"

    from crawler import handler as crawler_handler_module

    crawler_handler_module.RAW_DATA_BUCKET_NAME = BUCKET_NAME
    crawler_handler_module.RAW_FETCHED_QUEUE_URL = raw_fetched_queue_url

    event = {"Records": [{"body": m["Body"]} for m in messages]}
    result = crawler_lambda_handler(event, None)
    print(f"  crawler_lambda_handler result: {result}")

    for m in messages:
        sqs_client.delete_message(QueueUrl=crawl_queue_url, ReceiptHandle=m["ReceiptHandle"])

    time.sleep(0.2)
    raw_fetched = sqs_client.receive_message(QueueUrl=raw_fetched_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    raw_messages = raw_fetched.get("Messages", [])
    assert len(raw_messages) == 1, f"expected 1 raw-fetched message, got {len(raw_messages)}"
    for m in raw_messages:
        sqs_client.delete_message(QueueUrl=raw_fetched_queue_url, ReceiptHandle=m["ReceiptHandle"])

    return json.loads(raw_messages[0]["Body"])


def _query_domain_row(domain):
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM domains WHERE domain = %s", (domain,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def main() -> int:
    print(f"targeting LocalStack at {LOCALSTACK_ENDPOINT_URL}")
    print(
        f"targeting Postgres at {os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )

    server = _start_server()
    domain = f"127.0.0.1:{server.server_address[1]}"
    print(f"local mock server (agents.json + Web Bot Auth present): http://{domain}")

    kwargs = crawler_boto3_client_kwargs()
    s3_client = boto3.client("s3", **kwargs)
    sqs_client = boto3.client("sqs", **kwargs)

    try:
        s3_client.create_bucket(Bucket=BUCKET_NAME)
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        pass
    crawl_queue_url = sqs_client.create_queue(QueueName=CRAWL_QUEUE_NAME)["QueueUrl"]
    raw_fetched_queue_url = sqs_client.create_queue(QueueName=RAW_FETCHED_QUEUE_NAME)["QueueUrl"]
    notify_queue_url = sqs_client.create_queue(QueueName=NOTIFY_QUEUE_NAME)["QueueUrl"]
    print(f"crawl-queue:   {crawl_queue_url}")
    print(f"raw-fetched:   {raw_fetched_queue_url}")
    print(f"notify-queue:  {notify_queue_url}")

    from parser import handler as parser_handler_module

    parser_handler_module.RAW_DATA_BUCKET_NAME = BUCKET_NAME
    parser_handler_module.NOTIFY_QUEUE_URL = notify_queue_url

    # ---- Step 1: seed -> real crawl -> real raw-fetched message -----------
    print("\n=== crawl #1 (key valid) ===")
    raw_fetched_message = _crawl_and_get_raw_fetched_message(
        domain, crawl_queue_url, raw_fetched_queue_url, sqs_client
    )
    print(f"  raw-fetched message: {raw_fetched_message}")
    assert raw_fetched_message["agents_json"]["present"] is True
    assert raw_fetched_message["web_bot_auth"]["present"] is True

    # ---- Step 2: feed that real message into parser-service's real --------
    #      lambda_handler.
    parser_event = {"Records": [{"body": json.dumps(raw_fetched_message)}]}
    parser_result = parser_lambda_handler(parser_event, None)
    print(f"  parser_lambda_handler result: {parser_result}")
    assert parser_result == {"processed": 1, "domains": [domain]}

    # ---- Step 3: query the real Postgres row back out ---------------------
    row = _query_domain_row(domain)
    print(f"\ndomains row after crawl #1:\n  {row}")
    assert row is not None, "expected a domains row after parser-service processed the crawl"
    assert row["agent_json_present"] is True
    assert row["web_bot_auth_present"] is True
    assert row["web_bot_auth_key_id"] == "verify-key-1"
    assert row["web_bot_auth_valid"] is True
    assert row["declared_capabilities"] == {
        "agents": [{"name": "verify-bot", "capabilities": ["chat", "search"]}]
    }
    assert row["confidence_flags"] == []
    first_seen_at = row["first_seen_at"]

    # No record-changed yet -- this is the first-ever crawl of this domain.
    no_messages_yet = sqs_client.receive_message(QueueUrl=notify_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
    assert not no_messages_yet.get("Messages"), "expected no record-changed on the very first crawl"
    print("OK: no record-changed published on first-ever crawl, as expected.")

    # ---- Step 4: crawl again, but the Web Bot Auth key is now expired ------
    print("\n=== crawl #2 (key now expired) ===")
    _KEY_EXPIRY_TS[0] = int((_NOW - timedelta(days=1)).timestamp())

    raw_fetched_message_2 = _crawl_and_get_raw_fetched_message(
        domain, crawl_queue_url, raw_fetched_queue_url, sqs_client
    )
    parser_event_2 = {"Records": [{"body": json.dumps(raw_fetched_message_2)}]}
    parser_result_2 = parser_lambda_handler(parser_event_2, None)
    print(f"  parser_lambda_handler result: {parser_result_2}")

    row_2 = _query_domain_row(domain)
    print(f"\ndomains row after crawl #2:\n  {row_2}")
    assert row_2["web_bot_auth_valid"] is False
    assert "expired_key" in row_2["confidence_flags"]
    # first_seen_at must be preserved across the second crawl.
    assert row_2["first_seen_at"] == first_seen_at
    assert row_2["last_crawled_at"] > row["last_crawled_at"]

    changed = sqs_client.receive_message(QueueUrl=notify_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    changed_messages = [json.loads(m["Body"]) for m in changed.get("Messages", [])]
    print(f"\nrecord-changed messages ({len(changed_messages)}): {changed_messages}")
    assert len(changed_messages) == 1
    assert changed_messages[0]["domain"] == domain
    assert "web_bot_auth_valid" in changed_messages[0]["changed_fields"]
    assert "confidence_flags" in changed_messages[0]["changed_fields"]

    print(
        "\nOK: seed -> crawl-queue -> crawler_lambda_handler -> S3 + raw-fetched "
        "-> parser_lambda_handler -> real Postgres `domains` row -> notify-queue "
        "on change, all verified against LocalStack + local Postgres."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
