#!/usr/bin/env python3
"""End-to-end verification of the FULL Phase 5 pipeline against real
LocalStack (S3 + SQS + DynamoDB) and a real local Postgres, using local
mock HTTP(S) servers instead of the real internet -- extends
parser-service's own Phase 2 `verify_localstack_e2e.py` one step
further: crawler -> parser -> notify-queue -> notifier-service's real
webhook delivery, plus api-service's real monitor-registration/export
routes exercised through the real FastAPI app (not mocks).

What this proves, end to end, using real AWS APIs (via LocalStack), a
real local Postgres, and the real FastAPI app object (via Starlette's
TestClient -- an in-process ASGI call, not a separate uvicorn socket;
see the note in `main()` for exactly what that does and doesn't prove):

  1. A domain is crawled for the first time (crawler -> S3 -> parser ->
     Postgres) -- same chain parser-service's own script already
     proves, reused here as setup, not re-verified line for line.
  2. A REAL, API-key-authenticated `POST /v1/monitors` call (through
     api-service's actual FastAPI app + real Postgres + real
     shared_utils SSRF validation) registers a webhook monitor for that
     domain.
  3. A REAL `POST /v1/monitors` call with a private/cloud-metadata
     webhook_url is REJECTED (400) -- the SSRF guard rejects it for
     real, not just in a unit test.
  4. The domain is crawled again with a changed signal -> parser
     publishes a real `record-changed` message to a real `notify-queue`.
  5. That real message is received off `notify-queue` and fed into
     notifier-service's real `lambda_handler` -- which looks up the
     real monitor row just created, validates its webhook_url again
     (the delivery-time SSRF re-check), and POSTs a real HTTPS request
     to a local mock webhook receiver, which is asserted to have
     actually received it with the expected payload.
  6. A REAL `POST /v1/export` call (licensing-tier key) dumps the
     `domains` table to a real S3 object on LocalStack and returns a
     presigned URL, which is fetched with a real HTTP GET (not through
     boto3) to confirm its actual NDJSON contents.

--- A necessary, clearly-labeled test-only bypass ------------------------

`shared_utils.webhook_safety.validate_webhook_url` correctly requires a
webhook host to resolve to a PUBLIC address -- by design, it would
reject any local mock server (loopback and private-network addresses
are exactly what it exists to block). To still demonstrate a REAL
delivery end to end, this script monkeypatches ONLY
`shared_utils.webhook_safety._default_resolve` (the module's internal
DNS-resolution helper) to report a fake public IP specifically for the
hostname "localhost" -- the validation LOGIC itself (scheme check, the
private/loopback/link-local/multicast/reserved/unspecified address
checks) is completely real and unmodified; only the DNS ANSWER for that
one hostname, as seen by THIS validator, is faked, and it is faked
identically for both api-service's registration-time check and
notifier-service's delivery-time re-check (proving they really do run
the same logic). The actual TCP connection is NOT affected by this
patch at all: httpx has no relationship to `shared_utils` and resolves
"localhost" via the real system resolver when it actually connects, so
the mock HTTPS server genuinely receives a real network connection --
this patch only changes what ONE specific validator function believes
"localhost" resolves to, not what it actually resolves to. Production
traffic gets no such bypass: this patch exists only inside this
script's process, only for the duration of this run, and never touches
the actual `webhook_safety.py` source. The unsafe-URL rejection
demonstrated in step 3 below uses a REAL address (169.254.169.254) that
this bypass does not special-case, so that rejection is completely
unaffected by the patch.

Prerequisites:
    docker compose up -d postgres localstack   # from the repo root
    (packages/shared-schema)$ DATABASE_URL=... alembic upgrade head
    openssl available on PATH (used to generate a throwaway self-signed
      cert for the local HTTPS webhook receiver -- see `_start_webhook_receiver`)

Usage (from services/notifier-service, with this service's,
crawler-service's, parser-service's, and api-service's requirements.txt
all installed in the same environment):
    python scripts/verify_localstack_e2e.py
"""

from __future__ import annotations

import http.server
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Configure env vars BEFORE importing anything from crawler/parser/ ------
# api/notifier (each package's config module reads these once, at import
# time -- same convention every prior phase's verification script uses).

LOCALSTACK_ENDPOINT_URL = os.environ.get("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
os.environ["AWS_ENDPOINT_URL"] = LOCALSTACK_ENDPOINT_URL
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ["CRAWLER_URL_SCHEME"] = "http"

BUCKET_NAME = os.environ.setdefault("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-verify-p5")
EXPORT_BUCKET_NAME = os.environ.setdefault("EXPORT_BUCKET_NAME", "agent-intel-exports-verify-p5")
CRAWL_QUEUE_NAME = "crawl-queue-verify-p5"
RAW_FETCHED_QUEUE_NAME = "raw-fetched-verify-p5"
NOTIFY_QUEUE_NAME = "notify-queue-verify-p5"
RATE_LIMIT_TABLE_NAME = os.environ.setdefault("RATE_LIMIT_TABLE_NAME", "agent-intel-rate-limit-verify-p5")

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "agent_intel")
os.environ.setdefault("DB_USER", "agent_intel")
os.environ.setdefault("DB_PASSWORD", "agent_intel")

THIS_DIR = Path(__file__).resolve().parent
NOTIFIER_SERVICE_DIR = THIS_DIR.parent
SERVICES_DIR = NOTIFIER_SERVICE_DIR.parent
CRAWLER_SERVICE_DIR = SERVICES_DIR / "crawler-service"
PARSER_SERVICE_DIR = SERVICES_DIR / "parser-service"
API_SERVICE_DIR = SERVICES_DIR / "api-service"

for path in (NOTIFIER_SERVICE_DIR, CRAWLER_SERVICE_DIR, PARSER_SERVICE_DIR, API_SERVICE_DIR):
    sys.path.insert(0, str(path))

import boto3  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from crawler.config import boto3_client_kwargs as crawler_boto3_client_kwargs  # noqa: E402
from crawler.handler import lambda_handler as crawler_lambda_handler  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from parser.handler import lambda_handler as parser_lambda_handler  # noqa: E402
from shared_utils import generate_api_key_secret, hash_api_key_secret  # noqa: E402
import shared_utils.webhook_safety as webhook_safety  # noqa: E402

from notifier import handler as notifier_handler  # noqa: E402

# The mock webhook receiver is addressed as "https://localhost:<port>/..."
# -- "localhost" is the ONE hostname `_demo_resolver` below special-cases.
# httpx's real connection resolves "localhost" via the real system
# resolver regardless of this patch (see module docstring); only what
# `validate_webhook_url` BELIEVES "localhost" resolves to is faked.
WEBHOOK_MAGIC_HOSTNAME = "localhost"
FAKE_PUBLIC_IP = "93.184.216.34"  # a real, public, non-sensitive IANA example address
MOCK_METADATA_URL = "https://169.254.169.254/latest/meta-data/iam/security-credentials/"

_REAL_DEFAULT_RESOLVE = webhook_safety._default_resolve


def _demo_resolver(hostname: str):
    """Test-only DNS-resolution bypass -- see module docstring's
    "necessary, clearly-labeled test-only bypass" section for exactly
    what this does and doesn't affect.
    """
    if hostname == WEBHOOK_MAGIC_HOSTNAME:
        return [FAKE_PUBLIC_IP]
    return _REAL_DEFAULT_RESOLVE(hostname)


# --- Mock "crawled site" server (agents.json / web-bot-auth / llms.txt) -----
# Mirrors parser-service's own verify_localstack_e2e.py exactly.


class _CrawledSiteHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/agents.json":
            body = json.dumps({"agents": [{"name": "verify-bot-p5", "capabilities": ["chat"]}]}).encode()
            self._send(200, "application/json", body)
        elif self.path == "/.well-known/http-message-signatures-directory":
            body = json.dumps({"keys": [{"kty": "OKP", "kid": "verify-key-p5", "exp": _KEY_EXPIRY_TS[0]}]}).encode()
            self._send(200, "application/jwk-set+json", body)
        elif self.path == "/llms.txt":
            self._send(200, "text/plain", b"# verify-bot-p5\nA demo site for Phase 5 verification.\n")
        else:
            self.send_response(404)
            self.end_headers()

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_NOW = datetime.now(timezone.utc)
_KEY_EXPIRY_TS = [int((_NOW + timedelta(days=30)).timestamp())]


def _start_crawled_site_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _CrawledSiteHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- Mock webhook receiver (HTTPS, self-signed cert) ------------------------


class _WebhookReceiverHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.received_payloads.append(json.loads(body))  # type: ignore[attr-defined]
        response = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def _generate_self_signed_cert(cert_dir: Path) -> tuple[Path, Path]:
    key_path = cert_dir / "key.pem"
    cert_path = cert_dir / "cert.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "1", "-nodes", "-subj", "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    return key_path, cert_path


def _start_webhook_receiver_server():
    cert_dir = Path(tempfile.mkdtemp())
    key_path, cert_path = _generate_self_signed_cert(cert_dir)

    server = http.server.HTTPServer(("127.0.0.1", 0), _WebhookReceiverHandler)
    server.received_payloads = []  # type: ignore[attr-defined]

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    server.socket = ssl_context.wrap_socket(server.socket, server_side=True)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- Postgres helpers --------------------------------------------------------


def _pg_connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def _insert_api_key(conn, *, key_id: str, plan_tier: str) -> str:
    """Insert a real, active `api_keys` row and return the PLAINTEXT
    bearer secret (never persisted -- only its hash is). Mirrors what
    billing-service's Stripe-checkout-driven issuance does, minus
    Stripe (out of scope here) -- this script IS the "out-of-band" path
    the `ApiKey.pending_secret` docstring already describes for
    licensing keys.
    """
    secret = generate_api_key_secret()
    key_hash = hash_api_key_secret(secret)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_keys (key_id, owner_email, plan_tier, rate_limit, key_hash, active, created_at)
            VALUES (%s, %s, %s, %s, %s, true, now())
            ON CONFLICT (key_id) DO NOTHING
            """,
            (key_id, f"{key_id}@example.test", plan_tier, 1000, key_hash),
        )
    conn.commit()
    return secret


def _query_domain_row(domain: str) -> dict | None:
    conn = _pg_connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM domains WHERE domain = %s", (domain,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def _crawl_and_parse(domain: str, *, crawl_queue_url, raw_fetched_queue_url, notify_queue_url, sqs_client) -> dict:
    sqs_client.send_message(QueueUrl=crawl_queue_url, MessageBody=json.dumps({"domain": domain}))
    received = sqs_client.receive_message(QueueUrl=crawl_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    messages = received.get("Messages", [])
    assert len(messages) == 1, f"expected 1 message off crawl-queue, got {len(messages)}"

    from crawler import handler as crawler_handler_module

    crawler_handler_module.RAW_DATA_BUCKET_NAME = BUCKET_NAME
    crawler_handler_module.RAW_FETCHED_QUEUE_URL = raw_fetched_queue_url

    event = {"Records": [{"body": m["Body"]} for m in messages]}
    crawler_lambda_handler(event, None)
    for m in messages:
        sqs_client.delete_message(QueueUrl=crawl_queue_url, ReceiptHandle=m["ReceiptHandle"])

    time.sleep(0.2)
    raw_fetched = sqs_client.receive_message(QueueUrl=raw_fetched_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    raw_messages = raw_fetched.get("Messages", [])
    assert len(raw_messages) == 1, f"expected 1 raw-fetched message, got {len(raw_messages)}"
    for m in raw_messages:
        sqs_client.delete_message(QueueUrl=raw_fetched_queue_url, ReceiptHandle=m["ReceiptHandle"])

    from parser import handler as parser_handler_module

    parser_handler_module.RAW_DATA_BUCKET_NAME = BUCKET_NAME
    parser_handler_module.NOTIFY_QUEUE_URL = notify_queue_url

    parser_event = {"Records": [{"body": raw_messages[0]["Body"]}]}
    result = parser_lambda_handler(parser_event, None)
    assert result == {"processed": 1, "domains": [domain]}
    return json.loads(raw_messages[0]["Body"])


def main() -> int:
    print(f"targeting LocalStack at {LOCALSTACK_ENDPOINT_URL}")
    print(f"targeting Postgres at {os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}")

    # --- Test-only SSRF-validator DNS bypass (see module docstring) --------
    webhook_safety._default_resolve = _demo_resolver

    site_server = _start_crawled_site_server()
    domain = f"127.0.0.1:{site_server.server_address[1]}"
    print(f"mock crawled-site server: http://{domain}")

    webhook_server = _start_webhook_receiver_server()
    webhook_port = webhook_server.server_address[1]
    webhook_url = f"https://{WEBHOOK_MAGIC_HOSTNAME}:{webhook_port}/callback"
    print(f"mock webhook receiver (HTTPS, self-signed): {webhook_url}")
    print("  ('localhost' resolves for real via the system resolver for the actual TCP connection; "
          "only the SSRF validator's belief about it is faked -- see module docstring)")

    kwargs = crawler_boto3_client_kwargs()
    s3_client = boto3.client("s3", **kwargs)
    sqs_client = boto3.client("sqs", **kwargs)
    dynamodb_client = boto3.client("dynamodb", **kwargs)

    try:
        s3_client.create_bucket(Bucket=BUCKET_NAME)
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        pass
    try:
        s3_client.create_bucket(Bucket=EXPORT_BUCKET_NAME)
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        pass

    crawl_queue_url = sqs_client.create_queue(QueueName=CRAWL_QUEUE_NAME)["QueueUrl"]
    raw_fetched_queue_url = sqs_client.create_queue(QueueName=RAW_FETCHED_QUEUE_NAME)["QueueUrl"]
    notify_queue_url = sqs_client.create_queue(QueueName=NOTIFY_QUEUE_NAME)["QueueUrl"]
    print(f"crawl-queue:  {crawl_queue_url}")
    print(f"raw-fetched:  {raw_fetched_queue_url}")
    print(f"notify-queue: {notify_queue_url}")

    try:
        dynamodb_client.create_table(
            TableName=RATE_LIMIT_TABLE_NAME,
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
    except dynamodb_client.exceptions.ResourceInUseException:
        pass

    # --- Seed real, active API keys (out-of-band, see _insert_api_key) -----
    conn = _pg_connect()
    self_serve_secret = _insert_api_key(conn, key_id="verify-p5-self-serve", plan_tier="self_serve")
    licensing_secret = _insert_api_key(conn, key_id="verify-p5-licensing", plan_tier="licensing")
    conn.close()
    print("seeded a real active self_serve key and a real active licensing key in api_keys")

    # === Step 1: first-ever crawl (setup, mirrors parser-service's own script) ===
    print("\n=== crawl #1 (key valid) ===")
    _crawl_and_parse(
        domain,
        crawl_queue_url=crawl_queue_url,
        raw_fetched_queue_url=raw_fetched_queue_url,
        notify_queue_url=notify_queue_url,
        sqs_client=sqs_client,
    )
    row = _query_domain_row(domain)
    assert row is not None and row["web_bot_auth_valid"] is True
    print(f"domain {domain} crawled and normalized; web_bot_auth_valid={row['web_bot_auth_valid']}")

    # === Step 2: real POST /v1/monitors through api-service's real app =====
    os.environ["RAW_DATA_BUCKET_NAME"] = BUCKET_NAME
    os.environ["EXPORT_BUCKET_NAME"] = EXPORT_BUCKET_NAME
    os.environ["RATE_LIMIT_TABLE_NAME"] = RATE_LIMIT_TABLE_NAME

    from app import app as api_app  # api-service's real FastAPI app

    client = TestClient(api_app)

    print("\n=== POST /v1/monitors: SSRF rejection (real) ===")
    rejected = client.post(
        "/v1/monitors",
        json={"domain": domain, "webhook_url": MOCK_METADATA_URL},
        headers={"X-API-Key": self_serve_secret},
    )
    print(f"  status={rejected.status_code} body={rejected.json()}")
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "unsafe_webhook_url"
    print("OK: a private/cloud-metadata webhook_url was rejected for real by the live API.")

    print("\n=== POST /v1/monitors: real registration ===")
    created = client.post(
        "/v1/monitors",
        json={"domain": domain, "webhook_url": webhook_url},
        headers={"X-API-Key": self_serve_secret},
    )
    print(f"  status={created.status_code} body={created.json()}")
    assert created.status_code == 201
    monitor_id = created.json()["monitor_id"]
    print(f"OK: monitor {monitor_id} registered for real via api-service.")

    # === Step 3: second crawl with a changed signal -> record-changed ======
    print("\n=== crawl #2 (key now expired) -> record-changed ===")
    _KEY_EXPIRY_TS[0] = int((_NOW - timedelta(days=1)).timestamp())
    _crawl_and_parse(
        domain,
        crawl_queue_url=crawl_queue_url,
        raw_fetched_queue_url=raw_fetched_queue_url,
        notify_queue_url=notify_queue_url,
        sqs_client=sqs_client,
    )

    changed = sqs_client.receive_message(QueueUrl=notify_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    changed_messages = changed.get("Messages", [])
    assert len(changed_messages) == 1, f"expected 1 record-changed message, got {len(changed_messages)}"
    print(f"  record-changed message: {changed_messages[0]['Body']}")

    # === Step 4: feed that real message into notifier-service's real handler ===
    print("\n=== notifier-service: real delivery ===")
    notify_event = {"Records": [{"body": changed_messages[0]["Body"]}]}

    # Test-only: use a verify=False client so the demo's self-signed cert
    # is accepted -- see notifier/handler.py's `get_http_client` docstring.
    import httpx

    original_get_http_client = notifier_handler.get_http_client
    notifier_handler.get_http_client = lambda: httpx.Client(verify=False)
    try:
        notify_result = notifier_handler.lambda_handler(notify_event, None)
    finally:
        notifier_handler.get_http_client = original_get_http_client

    print(f"  notifier lambda_handler result: {notify_result}")
    assert notify_result == {"processed": 1, "domains": [domain]}

    time.sleep(0.1)
    assert len(webhook_server.received_payloads) == 1, (
        f"expected the mock webhook receiver to have gotten exactly 1 POST, "
        f"got {len(webhook_server.received_payloads)}"
    )
    received_payload = webhook_server.received_payloads[0]
    print(f"  mock webhook receiver actually received: {received_payload}")
    assert received_payload["domain"] == domain
    assert received_payload["monitor_id"] == monitor_id
    assert "web_bot_auth_valid" in received_payload["changed_fields"]
    print("OK: notifier-service delivered a real HTTP POST with the expected payload to the mock webhook receiver.")

    sqs_client.delete_message(QueueUrl=notify_queue_url, ReceiptHandle=changed_messages[0]["ReceiptHandle"])

    # === Step 5: real POST /v1/export (licensing tier) ======================
    print("\n=== POST /v1/export: real presigned export ===")
    export_response = client.post("/v1/export", headers={"X-API-Key": licensing_secret})
    print(f"  status={export_response.status_code} body={export_response.json()}")
    assert export_response.status_code == 200
    export_body = export_response.json()
    assert export_body["domain_count"] >= 1

    with urllib.request.urlopen(export_body["url"]) as resp:
        exported_content = resp.read().decode("utf-8")
    exported_rows = [json.loads(line) for line in exported_content.strip().splitlines()]
    exported_domains = {row["domain"] for row in exported_rows}
    print(f"  fetched presigned export URL for real -- {len(exported_rows)} row(s), includes our domain: {domain in exported_domains}")
    assert domain in exported_domains
    print("OK: export produced a real presigned URL for a real S3 object, fetched and verified for real.")

    print(
        "\nOK: full Phase 5 pipeline verified against LocalStack + local Postgres -- "
        "crawl -> parse -> real monitor registration (+ real SSRF rejection) -> "
        "record-changed -> real notifier webhook delivery -> real licensing export."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
