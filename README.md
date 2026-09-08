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

This repo is being built in phases. **We are currently on Phase 0.**

0. **Foundations** (this phase) — repo scaffold, canonical schema as
   Pydantic models, network Terraform module + remote state bootstrap,
   CI, local dev via docker-compose. No crawler/parser/API logic yet.
1. Data model + persistence — Postgres schema/migrations, RDS Terraform
   module, real DB access from services.
2. Crawler + parser — real fetch/parse logic for agents.json, llms.txt,
   Web Bot Auth, on-chain registries; SQS wiring; S3 storage.
3. Public lookup API + frontend — real `api-service` query endpoints
   and a working lookup UI.
4. Billing + monitors — API key issuance, plan tiers/rate limiting,
   webhook notifications.
5. Production infra + scheduling — full AWS deployment (Lambda, API
   Gateway, CloudFront, Amplify, Secrets Manager), scheduled re-crawls,
   staging/prod environments.

## Running locally

### 1. Start Postgres + LocalStack

```bash
docker compose up -d
```

This starts:
- `postgres` (Postgres 16) on `localhost:5432`, user/db `agent_intel`
- `localstack` (S3, SQS, DynamoDB emulation) on `localhost:4566`

Both use named volumes (`postgres_data`, `localstack_data`) so data
persists across restarts. Check config validity any time with
`docker compose config`.

### 2. Run a Python service's health check

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

### 3. Run the frontend

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

`infra/terraform/modules/network` is the only fully implemented
Terraform module so far (VPC, public/private subnets, NAT gateway,
internet gateway, route tables — parameterized by region, CIDRs, AZ
count). The other modules (`rds`, `sqs`, `lambda`, `api_gateway`, `s3`,
`cloudfront`, `amplify`, `secrets`) are placeholders for later phases.

`infra/terraform/envs/dev/backend-bootstrap` provisions the S3 bucket +
DynamoDB table used for Terraform remote state (versioned + encrypted
bucket, pay-per-request lock table). `infra/terraform/envs/dev` is the
only environment that currently wires up that backend and calls the
`network` module; `staging`/`prod` are placeholders that will mirror
its structure later.

None of this Terraform has been applied — Phase 0 is writing the
config, not deploying it.
