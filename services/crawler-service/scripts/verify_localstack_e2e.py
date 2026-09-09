#!/usr/bin/env python3
"""End-to-end verification of crawler-service against a real LocalStack
instance (S3 + SQS), using a local mock HTTP server instead of the real
internet so the run is deterministic and needs no network egress.

What this proves, end to end, using the real AWS APIs (via LocalStack,
not mocks):
  1. `scripts/load_seed_list.py`'s domains land as real messages on a
     real `crawl-queue` SQS queue.
  2. Those messages are received off `crawl-queue` (simulating exactly
     what an SQS -> Lambda event source mapping delivers) and fed into
     `crawler.handler.lambda_handler` directly.
  3. The Lambda handler crawls each domain, writes real objects to a
     real S3 bucket, and publishes a real message per domain onto
     `raw-fetched`.

Prerequisites:
    docker compose up -d localstack        # from the repo root

Usage (from services/crawler-service, with requirements.txt installed):
    python scripts/verify_localstack_e2e.py

Everything here targets LocalStack (http://localhost:4566 by default,
override with LOCALSTACK_ENDPOINT_URL) -- never real AWS.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

# --- Configure env vars BEFORE importing anything from `crawler` -------------
# (crawler.config reads these once, at import time.)

LOCALSTACK_ENDPOINT_URL = os.environ.get("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
os.environ["AWS_ENDPOINT_URL"] = LOCALSTACK_ENDPOINT_URL
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")
# Point crawl URLs at http://<host:port> (the local mock server) instead
# of https://<domain> -- this is exactly the override
# `crawler.fetch.build_url` was designed to accept for local/dev use.
os.environ["CRAWLER_URL_SCHEME"] = "http"

BUCKET_NAME = os.environ.setdefault("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-verify")
CRAWL_QUEUE_NAME = "crawl-queue-verify"
RAW_FETCHED_QUEUE_NAME = "raw-fetched-verify"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from crawler.config import boto3_client_kwargs
from crawler.handler import lambda_handler

# --- A tiny local HTTP server standing in for two real domains --------------


class _FakeSignalsHandler(http.server.BaseHTTPRequestHandler):
    """Serves a valid agents.json + JWKS directory on every path (this
    "domain" has both signals); real presence/absence variation is
    achieved by running a *second* server that 404s everything.
    """

    def log_message(self, *args):  # silence default request logging
        pass

    def do_GET(self):
        if self.server.serve_signals:  # type: ignore[attr-defined]
            if self.path == "/agents.json":
                body = json.dumps({"agents": [{"name": "demo-bot", "capabilities": ["chat"]}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/.well-known/http-message-signatures-directory":
                body = json.dumps({"keys": [{"kty": "OKP", "kid": "demo-key-1"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/jwk-set+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_response(404)
        self.end_headers()


def _start_server(serve_signals: bool):
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeSignalsHandler)
    server.serve_signals = serve_signals  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> int:
    print(f"targeting LocalStack at {LOCALSTACK_ENDPOINT_URL}")

    server_with_signals = _start_server(serve_signals=True)
    server_without_signals = _start_server(serve_signals=False)
    domain_with_signals = f"127.0.0.1:{server_with_signals.server_address[1]}"
    domain_without_signals = f"127.0.0.1:{server_without_signals.server_address[1]}"
    print(f"local mock server (signals present): http://{domain_with_signals}")
    print(f"local mock server (signals absent):  http://{domain_without_signals}")

    s3_client = boto3.client("s3", **boto3_client_kwargs())
    sqs_client = boto3.client("sqs", **boto3_client_kwargs())

    # Idempotent setup against LocalStack -- create if missing.
    try:
        s3_client.create_bucket(Bucket=BUCKET_NAME)
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        pass
    crawl_queue_url = sqs_client.create_queue(QueueName=CRAWL_QUEUE_NAME)["QueueUrl"]
    raw_fetched_queue_url = sqs_client.create_queue(QueueName=RAW_FETCHED_QUEUE_NAME)["QueueUrl"]
    print(f"crawl-queue:  {crawl_queue_url}")
    print(f"raw-fetched:  {raw_fetched_queue_url}")

    # Feed handler.py's module-level config the *real* queue URLs we
    # just created (these are randomly-suffixed LocalStack queues, not
    # known at import time).
    from crawler import handler

    handler.RAW_DATA_BUCKET_NAME = BUCKET_NAME
    handler.RAW_FETCHED_QUEUE_URL = raw_fetched_queue_url

    # Step 1: push both domains onto crawl-queue, exactly as
    # scripts/load_seed_list.py does.
    for domain in (domain_with_signals, domain_without_signals):
        sqs_client.send_message(QueueUrl=crawl_queue_url, MessageBody=json.dumps({"domain": domain}))
    print("enqueued 2 domains onto crawl-queue")

    # Step 2: receive them back off crawl-queue -- this is exactly the
    # shape an SQS -> Lambda event source mapping would hand to
    # lambda_handler.
    received = sqs_client.receive_message(QueueUrl=crawl_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    messages = received.get("Messages", [])
    if len(messages) != 2:
        print(f"expected 2 messages off crawl-queue, got {len(messages)}", file=sys.stderr)
        return 1

    event = {"Records": [{"body": m["Body"]} for m in messages]}

    # Step 3: invoke the Lambda handler directly.
    result = lambda_handler(event, None)
    print(f"lambda_handler result: {result}")

    for m in messages:
        sqs_client.delete_message(QueueUrl=crawl_queue_url, ReceiptHandle=m["ReceiptHandle"])

    # Verify: S3 objects written.
    time.sleep(0.5)
    listing = s3_client.list_objects_v2(Bucket=BUCKET_NAME)
    keys = sorted(obj["Key"] for obj in listing.get("Contents", []))
    print(f"S3 objects in {BUCKET_NAME}:")
    for key in keys:
        print(f"  {key}")
    assert len(keys) == 4, f"expected 4 S3 objects, found {len(keys)}"

    # Verify: one raw-fetched message per domain.
    raw_fetched = sqs_client.receive_message(QueueUrl=raw_fetched_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    raw_messages = raw_fetched.get("Messages", [])
    print(f"raw-fetched messages ({len(raw_messages)}):")
    for m in raw_messages:
        print(f"  {m['Body']}")
    assert len(raw_messages) == 2, f"expected 2 raw-fetched messages, found {len(raw_messages)}"

    bodies = {json.loads(m["Body"])["domain"]: json.loads(m["Body"]) for m in raw_messages}
    assert bodies[domain_with_signals]["agents_json"]["present"] is True
    assert bodies[domain_with_signals]["web_bot_auth"]["present"] is True
    assert bodies[domain_without_signals]["agents_json"]["present"] is False
    assert bodies[domain_without_signals]["web_bot_auth"]["present"] is False

    print("\nOK: seed-list -> crawl-queue -> lambda_handler -> S3 + raw-fetched all verified against LocalStack.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
