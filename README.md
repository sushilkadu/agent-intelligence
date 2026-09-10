# Agent Intelligence

Agent Intelligence crawls public AI-agent identity signals across the
web — Web Bot Auth signature cards, `agents.json` manifests, `llms.txt`
files, and on-chain agent registries — normalizes them into one
canonical schema, and serves the result through a free public lookup
tool and a paid API.

## Architecture

| Component | Language | Role |
|---|---|---|
| `apps/frontend` | Next.js (TypeScript) | Public lookup UI |
| `services/crawler-service` | Python | Fetches raw signals (agents.json, llms.txt, Web Bot Auth cards, on-chain refs) for a domain |
| `services/parser-service` | Python | Normalizes raw crawl artifacts into the canonical `Domain` schema |
| `services/scheduler-service` | Python | Decides which domains are due for re-crawl and enqueues them |
| `services/api-service` | Python (FastAPI) | Public + paid lookup API, reads normalized data |
| `services/billing-service` | Python (FastAPI) | API key issuance, plan tiers, rate limits |
| `services/notifier-service` | Python | Sends webhook notifications when a monitored domain's signals change |
| `packages/shared-schema` | Python | Canonical Pydantic models (`Domain`, `ApiKey`, `Monitor`) shared by every Python service |
| `packages/shared-utils` | Python | Shared helpers (logging, etc.) |
| `infra/terraform` | Terraform | AWS infrastructure as code |

**Data flow (target end state):** `scheduler-service` enqueues due
domains → `crawler-service` fetches raw artifacts and stores them in S3
→ `parser-service` normalizes them into the canonical schema in
Postgres → `api-service` serves lookups from that data (free tier +
paid tier gated by `billing-service`-issued keys) → `notifier-service`
fires webhooks to `monitors` when a watched domain's data changes →
`apps/frontend` is the public-facing lookup UI.

## Build phases

This repo was built in phases. **All 6 phases (0–5) are complete.**

0. **Foundations** — repo scaffold, canonical schema as Pydantic
   models, network Terraform module + remote state bootstrap, CI,
   local dev via docker-compose. No crawler/parser/API logic yet.
1. **Crawler** — real async fetch logic for `agents.json` and the Web
   Bot Auth signature-agent-card (JWKS) directory; raw results stored
   in S3; a `raw-fetched` SQS event per crawled domain; an
   SQS(`crawl-queue`)-triggered Lambda handler; a seed-list loader
   script; S3/SQS/Lambda Terraform modules. See
   `services/crawler-service/README.md`.
2. **Parser + persistence** — normalizes raw crawl artifacts into the
   canonical schema, confidence flags (`expired_key`,
   `malformed_manifest`, `no_signals`, ...), Postgres schema via
   Alembic migrations, an Aurora Serverless v2 RDS Terraform module,
   change detection publishing to `notify-queue`.
3. **Public lookup API + frontend** — `GET /v1/domains/{domain}` and
   `/history`, per-IP rate limiting, a working Next.js lookup UI,
   API Gateway (HTTP API) + Amplify Hosting Terraform modules.
4. **Billing + auth** — hashed API keys, `POST /v1/domains/bulk`,
   tiered per-key rate limiting, Stripe Checkout/webhook handling in
   `billing-service`, an authenticated dashboard.
5. **Monitoring + licensing + broader coverage** — `notifier-service`
   (SSRF-safe webhook delivery with retry/backoff), monitor
   registration with ownership enforcement, `scheduler-service`
   (tiered re-crawl cadence), `llms.txt` crawling, a documented
   on-chain-registry stub, and a licensing-only bulk export endpoint.

None of this has been deployed to real AWS — every phase's Terraform
is written but never `apply`'d (see the Terraform section below). For
running the whole system locally instead, see the docker-compose
section below.

## Running the whole system locally (docker compose)

This brings up every service in this table -- Postgres, LocalStack,
all six Python services, and the frontend -- as real, long-running
containers you can click around against, not just the piecemeal
per-phase verification scripts (`services/*/scripts/verify_localstack_e2e.py`)
that exercise one phase's slice in isolation.

### 1. Bring the stack up

```bash
docker compose up -d --build
```

This builds and starts, in dependency order:
- `postgres` (Postgres 16) on `localhost:5432`, user/db `agent_intel`.
- `localstack` (S3, SQS, DynamoDB emulation) on `localhost:4566`.
  Pinned to `localstack/localstack:3.0` (Community edition) -- the
  `latest` tag requires a paid LocalStack auth token.
- `localstack-setup` (one-shot, exits 0): idempotently provisions every
  LocalStack resource the other services need -- the raw-crawl and
  export S3 buckets, `crawl-queue`/`raw-fetched`/`notify-queue` (+
  DLQs for the first and third), and the rate-limit DynamoDB table.
  See `infra/localstack/setup.py` for exactly which names it creates
  (a deliberately-picked consistent set -- see that file's docstring
  on the pre-existing drift between Terraform's resource names and
  the Python services' own `config.py` defaults).
- `migrate` (one-shot, exits 0): runs `alembic upgrade head`
  (`packages/shared-schema`) against `postgres`. Safe to re-run.
- `api-service` (`localhost:8000`) and `billing-service`
  (`localhost:8001`): real `uvicorn` processes, the same FastAPI apps
  used in `app.py`/production.
- `crawler-service`, `parser-service`, `notifier-service`: these are
  Lambda handlers in production, triggered by an SQS event source
  mapping. Locally they run `scripts/run_sqs_worker.py` -- a small
  adapter that long-polls the relevant queue and feeds each batch into
  the exact same, unchanged `*.handler.lambda_handler`, deleting
  messages only on success (an exception leaves them for SQS's own
  redelivery/DLQ, matching real Lambda semantics). No business logic
  lives in these scripts.
- `scheduler-service`: EventBridge-cron-triggered (hourly) in
  production. Locally it runs `scripts/run_periodic.py`, which calls
  the same `scheduler.handler.lambda_handler` on a loop every
  `SCHEDULER_INTERVAL_SECONDS` (default 90s in `docker-compose.yml`).
  **This 90s cadence is local-testing-only** -- it makes a scheduler
  run observable in a short manual session; production stays hourly.
- `frontend` (`localhost:3000`): `next dev` (not a production build),
  for interactive testing.

Watch everything come up with `docker compose ps` (`localstack-setup`
and `migrate` should show `Exited (0)`, everything else `Up`/`healthy`).
Check compose file validity any time with `docker compose config`.

### 2. Seed a real crawl

`crawler-service` makes REAL outbound HTTPS requests to whatever
domain you seed -- this stack is for genuine manual testing, unlike
the sandboxed/mocked verification scripts, so pick real domains you're
comfortable crawling a couple of times. Reuse the existing seed-list
loader (don't duplicate it) via `docker compose exec`:

```bash
echo "example.com" > /tmp/seed.txt
docker compose cp /tmp/seed.txt crawler-service:/tmp/seed.txt
docker compose exec crawler-service python scripts/load_seed_list.py /tmp/seed.txt
```

(`docker compose run --rm crawler-service python scripts/load_seed_list.py ...`
works too, with the file baked into a bind mount/image instead of
`docker compose cp`'d in.) This does **not** run automatically on
`docker compose up` -- auto-crawling on every startup would be
surprising.

Watch it flow through the pipeline:

```bash
docker compose logs -f crawler-service parser-service
```

then confirm the row landed in Postgres and is servable from the real
running `api-service`:

```bash
docker compose exec postgres psql -U agent_intel -d agent_intel \
  -c "select domain, last_crawled_at, agent_json_present, web_bot_auth_present from domains where domain = 'example.com';"

curl -s http://localhost:8000/v1/domains/example.com | python3 -m json.tool
```

Open `http://localhost:3000` and look the same domain up there -- it
calls the real running `api-service` over `NEXT_PUBLIC_API_BASE_URL`.

### 3. Billing / API keys without a real Stripe account

There is no real Stripe account or network access to `api.stripe.com`
in this environment (`billing-service`'s container starts fine with
harmless placeholder `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET`
values -- see `docker-compose.yml`). Real Checkout/Portal calls
(`POST /v1/billing/checkout`, `POST /v1/billing/portal`) will fail
against the real Stripe API -- that's expected, not a bug.

What DOES work end to end locally: webhook-signature-verified
synthetic events, the same technique `tests/test_webhook_signature.py`
and the Phase 4/5 verification scripts already prove for real (pure
local HMAC-SHA256 crypto, no network access needed). This drives the
real key-issuance path through the real running `billing-service`
container:

```bash
docker compose exec billing-service python3 -c "
import hashlib, hmac, json, time, urllib.request

secret = 'whsec_local_placeholder_not_real'  # matches docker-compose.yml
event = {
    'id': 'evt_local_test_1',
    'type': 'checkout.session.completed',
    'data': {'object': {
        'id': 'cs_local_test_1',
        'customer': 'cus_local_test_1',
        'customer_details': {'email': 'you@example.com'},
    }},
}
payload = json.dumps(event).encode()
ts = int(time.time())
sig = hmac.new(secret.encode(), f'{ts}.{payload.decode()}'.encode(), hashlib.sha256).hexdigest()
req = urllib.request.Request(
    'http://localhost:8001/v1/billing/webhook',
    data=payload,
    headers={'Stripe-Signature': f't={ts},v1={sig}', 'Content-Type': 'application/json'},
    method='POST',
)
print(urllib.request.urlopen(req).read())
"
```

This creates a real, active `api_keys` row (`self_serve` tier) for
`you@example.com`. Fetch its plaintext secret the same way billing
issuance normally surfaces it (`GET /v1/billing/session/{checkout_session_id}`
with `cs_local_test_1`), or read `pending_secret` directly from
Postgres for a quick local shortcut, then use it against the paid
endpoints:

```bash
curl -s http://localhost:8001/v1/billing/session/cs_local_test_1
curl -s -H "X-API-Key: <secret from above>" http://localhost:8000/v1/keys/me
curl -s -H "X-API-Key: <secret from above>" -X POST http://localhost:8000/v1/domains/bulk \
  -H "Content-Type: application/json" -d '{"domains": ["example.com"]}'
```

The dashboard at `http://localhost:3000/dashboard` accepts the same
key pasted into its "sign in" field.

### 4. Monitors + notifier-service

`POST /v1/monitors` (needs a `self_serve`/`licensing`-tier key, see
above) registers a webhook. To see `notifier-service` actually fire,
you need something listening for the POST -- any local HTTP listener
works, e.g. a one-liner in a second terminal:

```bash
python3 -m http.server 8080
```

`shared_utils.webhook_safety` rejects loopback/private-network
webhook URLs by design (SSRF protection) -- a plain
`http://localhost:8080/...` webhook_url will be correctly rejected by
`POST /v1/monitors`, same as it is in production. Use a webhook
receiver with a real public hostname (e.g. a free
[webhook.site](https://webhook.site) URL) if you want to see a real
delivery end to end locally without also standing up the DNS-bypass
`services/notifier-service/scripts/verify_localstack_e2e.py` uses for
its own sandboxed run.

Then re-crawl the domain with a changed signal (or just wait) and
watch `docker compose logs -f notifier-service`.

### 5. Triggering scheduler-service without waiting

`scheduler-service`'s container already loops every
`SCHEDULER_INTERVAL_SECONDS` (90s by default), so `docker compose logs
-f scheduler-service` will show a run within that window. To trigger
one immediately instead of waiting:

```bash
docker compose exec scheduler-service python -c "from scheduler.handler import lambda_handler; print(lambda_handler(None, None))"
```

### 6. Bringing it down

```bash
docker compose down       # stop + remove containers, KEEP postgres_data/localstack_data
docker compose down -v    # also delete both volumes -- full reset
```

### Running one Python service outside docker (unit tests, quick iteration)

Each Python service depends on `packages/shared-schema` and
`packages/shared-utils` as local editable installs (declared via
relative paths in each service's `requirements.txt`), so install from
inside the service's own directory:

```bash
cd services/api-service        # or billing-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload        # then: curl localhost:8000/health
```

```bash
cd services/crawler-service     # or parser-service, scheduler-service, notifier-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py                  # prints {'status': 'ok'}
```

Run tests for any service with `pytest -q` from inside that service's
directory.

`crawler-service` additionally has real crawl logic, a seed-list
loader, and a Lambda handler invoked by an SQS event source mapping in
production -- see `services/crawler-service/README.md` for how to run
it end to end against LocalStack, and each other service's own
`scripts/verify_localstack_e2e.py` for that phase's own sandboxed,
mocked-HTTP end-to-end proof.

### Running the frontend outside docker

```bash
cd apps/frontend
npm install
npm run dev      # http://localhost:3000
```

## CI

`.github/workflows/ci.yml` runs, per pull request: `ruff check` +
`pytest` for each of the six Python services (as a matrix job), and
`npm run lint` + `npm run build` for the frontend.

## Infrastructure

`infra/terraform/modules/network`, `s3`, `sqs`, and `lambda` are fully
implemented Terraform modules. `network` provisions the VPC,
public/private subnets, NAT gateway, internet gateway, and route
tables (parameterized by region, CIDRs, AZ count). `s3` provisions a
versioned, encrypted, fully-private bucket. `sqs` provisions a queue
with an optional dead-letter queue. `lambda` provisions a function (+
CloudWatch log group) with an optional SQS event source mapping; it
does not create IAM roles or build/upload deployment packages itself
-- both are the caller's responsibility, so the module stays reusable.
`rds` provisions an Aurora Serverless v2 Postgres cluster. `dynamodb`
provisions an on-demand table (used for rate limiting). `api_gateway`
provisions an HTTP API + Lambda proxy integration + route + throttled
stage. `secrets` and `amplify` are fleshed out and used by
billing-service and the frontend respectively. `cloudfront` remains an
intentional placeholder -- Amplify already fronts the frontend with
its own CDN, and there's no real domain/ACM cert yet to justify
putting CloudFront in front of API Gateway.

`infra/terraform/envs/dev/backend-bootstrap` provisions the S3 bucket +
DynamoDB table used for Terraform remote state (versioned + encrypted
bucket, pay-per-request lock table). `infra/terraform/envs/dev` wires
up that backend, calls the `network` module, and then every other
module above to provision the full dev environment across all 5
build phases: crawler-service's bucket/queues/Lambda, parser-service's
RDS cluster/Lambda/`notify-queue`, api-service's HTTP API/Lambda/rate-limit
table/export bucket, billing-service's HTTP API/Lambda/Stripe secrets,
notifier-service's Lambda, and scheduler-service's Lambda + hourly
EventBridge rule -- each with its own least-privilege IAM role scoped
to specific resource ARNs (no wildcards) and its own CloudWatch log
group. `staging`/`prod` are still placeholders that will mirror this
structure later.

None of this Terraform has ever been `plan`'d or `apply`'d against
real AWS (the `terraform` CLI isn't installed in the environment this
was built in) — every module above is written and structurally
reviewed, but unvalidated against a real provider. Every Lambda module
call also references a deployment package path (e.g.
`services/crawler-service/dist/crawler-service.zip`) that doesn't
exist yet; no build/CI step produces these zips, so a real `apply`
would fail until packaging is added. See the docker-compose section
below for how to actually run and test the system locally instead of
via this Terraform.
