# crawler-service

Fetches two agent-identity signals for a domain:

- `https://{domain}/agents.json`
- `https://{domain}/.well-known/http-message-signatures-directory` --
  the Web Bot Auth "signature-agent-card" JWKS directory, per the
  current IETF drafts (`draft-meunier-web-bot-auth-architecture`,
  `draft-meunier-http-message-signatures-directory`,
  `draft-meunier-webbotauth-registry`). This path is a single named
  constant, `WEB_BOT_AUTH_WELL_KNOWN_PATH` in `crawler/config.py`,
  since the draft is still moving.

Both fetches are raw and unopinionated: no JSON parsing, no
validation, no normalization. That's parser-service's job (Phase 2).
Anything other than a clean HTTP 200 (404, 5xx, timeout, DNS/connection
failure) is treated as "not present" -- the normal, expected outcome
for most domains, never an exception.

## What Phase 1 does, end to end

1. `crawler/fetch.py` -- async httpx fetch of both signals for a domain.
2. `crawler/storage.py` -- stores each fetch as a JSON envelope object
   in S3 (status code, headers, error, base64-encoded raw body), keyed
   `{domain}/{iso-timestamp}/{artifact}`, e.g.
   `example.com/2026-09-09T12:00:00+00:00/agents.json`.
3. `crawler/messaging.py` -- publishes one `raw-fetched` SQS message
   per crawled domain: the domain, presence booleans + status codes
   for each signal, and the S3 keys written.
4. `crawler/handler.py` -- `lambda_handler(event, context)`, triggered
   in production by an SQS (`crawl-queue`) event source mapping, one
   domain per message. Parses each record, crawls the domain, stores
   both artifacts, publishes `raw-fetched`. Runs the async crawl via
   `asyncio.run` since Lambda handlers are synchronous.
5. `scripts/load_seed_list.py` -- pushes a list of domains (one per
   line, or a CSV column) onto `crawl-queue`.

All AWS access goes through `AWS_ENDPOINT_URL`: set it to LocalStack's
endpoint for local dev, leave it unset for real AWS/Lambda. No code
change either way.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `AWS_ENDPOINT_URL` | (unset) | Set to `http://localhost:4566` to target LocalStack; unset targets real AWS. |
| `AWS_REGION` | `us-east-1` | Region for boto3 clients. |
| `RAW_DATA_BUCKET_NAME` | `agent-intel-raw-crawl-dev` | S3 bucket for raw crawl artifacts. |
| `CRAWL_QUEUE_URL` | (unset) | Queue URL used by `scripts/load_seed_list.py`. |
| `RAW_FETCHED_QUEUE_URL` | (unset) | Queue the Lambda handler publishes to. |
| `CRAWLER_TIMEOUT_SECONDS` | `10` | Per-request HTTP timeout. |
| `CRAWLER_URL_SCHEME` | `https` | Override to `http` to point crawl URLs at a local mock server instead of doing a real TLS handshake (dev/test only). |

## Running the tests

```bash
cd services/crawler-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

All 25 tests run against mocked HTTP (`respx`) and mocked AWS (`moto`)
-- no real network or AWS access. Covers: agents.json and Web Bot Auth
directory each present/valid, missing (404), malformed (200 with
invalid JSON/JWKS -- Phase 1 stores it raw either way), and
unreachable (timeout, DNS/connection failure); S3 key layout and
envelope shape; SQS message shape; and a full in-process
`lambda_handler` run.

## Running the full flow against LocalStack

```bash
# from the repo root
docker compose up -d localstack

# from services/crawler-service, with requirements.txt installed
python scripts/verify_localstack_e2e.py
```

This script creates a real bucket + two real SQS queues on LocalStack,
starts two tiny local HTTP servers (one serving valid signals, one
serving neither) standing in for two "domains" (so the run is
deterministic and needs no internet egress), pushes both onto
`crawl-queue`, receives them back off it exactly as an SQS -> Lambda
event source mapping would, calls `lambda_handler` directly, then
verifies: 4 S3 objects were written and 2 `raw-fetched` messages
arrived with the expected presence flags.

To exercise `scripts/load_seed_list.py` itself against a real queue:

```bash
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1
export CRAWL_QUEUE_URL=$(aws --endpoint-url=http://localhost:4566 sqs create-queue --queue-name crawl-queue --query QueueUrl --output text)
python scripts/load_seed_list.py scripts/seed_domains.txt
```

`scripts/seed_domains.txt` has ~15 sample domains for local testing.
Scaling to the target 500-1000 domain seed list needs no code change,
just a bigger input file (plain text, one domain per line, or a CSV
with `--column`).

Note: pushing the sample seed list onto `crawl-queue` only enqueues
domain names -- it does not crawl them. Don't feed the resulting queue
into `lambda_handler` against those real domains as part of routine
testing; use `verify_localstack_e2e.py`'s local mock servers instead.
If you want a real-network sanity check, do it manually against at
most 2-3 known-good domains.

## Terraform

`infra/terraform/envs/dev/main.tf` provisions this service's
infrastructure via the `s3`, `sqs`, and `lambda` modules: the raw crawl
bucket, `crawl-queue` (with a DLQ) and `raw-fetched`, the crawler
Lambda, and an IAM role scoped only to those three resources. Not
applied -- see the repo root README's Infrastructure section.
