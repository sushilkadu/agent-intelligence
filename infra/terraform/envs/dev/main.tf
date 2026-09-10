# Dev environment root module.
#
# Phase 0 scope: wires up the remote state backend (created by
# ../backend-bootstrap) and calls the network module.
#
# Phase 1 scope: adds crawler-service's infrastructure -- the raw crawl
# data S3 bucket, the `crawl-queue` / `raw-fetched` SQS queues, and the
# crawler Lambda (triggered by `crawl-queue`) with an IAM role scoped
# ONLY to those three resources (no wildcard resource ARNs). RDS/API
# Gateway/etc. modules still exist as empty placeholders under
# ../../modules and are not called from any env yet.
#
# NOT applied as part of Phase 0 or Phase 1. Do not `terraform
# init`/`plan`/`apply` this against real AWS.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # These values mirror the bucket/table created by
    # ../backend-bootstrap/main.tf. Terraform backend blocks can't
    # interpolate variables, so they're duplicated here literally -- keep
    # them in sync if the bootstrap config's names ever change.
    bucket         = "agent-intelligence-tfstate-dev"
    key            = "envs/dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "agent-intelligence-tfstate-lock-dev"
    encrypt        = true
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  description = "AWS region for the dev environment."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the dev VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones for the dev network."
  type        = number
  default     = 2
}

module "network" {
  source = "../../modules/network"

  name     = "agent-intel-dev"
  region   = var.region
  vpc_cidr = var.vpc_cidr
  az_count = var.az_count

  # Single shared NAT gateway keeps dev cheap; prod should set this false.
  single_nat_gateway = true

  tags = {
    Environment = "dev"
  }
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "public_subnet_ids" {
  value = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

# ---------------------------------------------------------------------------
# Phase 1: crawler-service infrastructure
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

locals {
  crawler_lambda_name = "agent-intel-dev-crawler"

  crawler_tags = {
    Environment = "dev"
    Service     = "crawler-service"
  }
}

# --- Raw crawl data bucket ---------------------------------------------------
# Keyed by crawler-service as {domain}/{iso-timestamp}/{artifact}, e.g.
# example.com/2026-09-09T12:00:00+00:00/agents.json

module "raw_crawl_bucket" {
  source = "../../modules/s3"

  name = "agent-intelligence-raw-crawl-dev"
  tags = local.crawler_tags
}

# --- Queues -------------------------------------------------------------

# Domains awaiting crawl, one message each (pushed by
# scripts/load_seed_list.py or, in a later phase, scheduler-service).
# Triggers the crawler Lambda via an SQS event source mapping. A DLQ is
# enabled so a domain whose crawl always errors can't block the queue.
module "crawl_queue" {
  source = "../../modules/sqs"

  name       = "crawl-queue"
  enable_dlq = true

  # Should comfortably exceed the crawler Lambda's timeout so a message
  # can't become visible to another consumer mid-processing.
  visibility_timeout_seconds = 90

  tags = local.crawler_tags
}

# One message per domain once crawler-service finishes fetching both
# signals for it. Consumed by parser-service in Phase 2.
module "raw_fetched_queue" {
  source = "../../modules/sqs"

  name = "raw-fetched"
  tags = local.crawler_tags
}

# --- IAM: least-privilege crawler Lambda execution role ----------------------
#
# Scoped to exactly three things, each by its specific ARN (no wildcard
# resource ARNs): consume crawl-queue, publish to raw-fetched, and
# read/write objects in the raw crawl bucket. Plus the minimum
# CloudWatch Logs permissions Lambda needs to run at all, scoped to
# this function's own log group.

data "aws_iam_policy_document" "crawler_lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "crawler_lambda" {
  name               = "${local.crawler_lambda_name}-role"
  assume_role_policy = data.aws_iam_policy_document.crawler_lambda_assume_role.json

  tags = local.crawler_tags
}

data "aws_iam_policy_document" "crawler_lambda_permissions" {
  statement {
    sid       = "ConsumeCrawlQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.crawl_queue.queue_arn]
  }

  statement {
    sid       = "PublishRawFetched"
    actions   = ["sqs:SendMessage"]
    resources = [module.raw_fetched_queue.queue_arn]
  }

  statement {
    sid       = "ReadWriteRawCrawlBucketObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${module.raw_crawl_bucket.bucket_arn}/*"]
  }

  statement {
    sid     = "WriteOwnCloudWatchLogs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.crawler_lambda_name}:*"
    ]
  }
}

resource "aws_iam_role_policy" "crawler_lambda" {
  name   = "${local.crawler_lambda_name}-policy"
  role   = aws_iam_role.crawler_lambda.id
  policy = data.aws_iam_policy_document.crawler_lambda_permissions.json
}

# --- Crawler Lambda -------------------------------------------------------

module "crawler_lambda" {
  source = "../../modules/lambda"

  name        = local.crawler_lambda_name
  description = "Fetches agents.json + the Web Bot Auth JWKS directory for domains on crawl-queue, stores raw results in S3, publishes raw-fetched events."
  handler     = "crawler.handler.lambda_handler"
  runtime     = "python3.11"
  timeout     = 60
  memory_size = 256
  role_arn    = aws_iam_role.crawler_lambda.arn

  # Placeholder path -- the deployment package (crawler-service +
  # its dependencies zipped up) is produced by a build/CI step that
  # doesn't exist yet. Phase 1 is writing this Terraform structurally,
  # not applying it; a real `terraform apply` needs this file to exist
  # first.
  filename = "${path.module}/../../../../services/crawler-service/dist/crawler-service.zip"

  environment_variables = {
    RAW_DATA_BUCKET_NAME  = module.raw_crawl_bucket.bucket_id
    RAW_FETCHED_QUEUE_URL = module.raw_fetched_queue.queue_id
    AWS_REGION            = var.region
    # Deliberately no AWS_ENDPOINT_URL here -- that's local-dev-only
    # (see services/crawler-service/crawler/config.py) and must never
    # be set on the real deployed function.
  }

  event_source_arn        = module.crawl_queue.queue_arn
  event_source_batch_size = 10

  depends_on = [aws_iam_role_policy.crawler_lambda]

  tags = local.crawler_tags
}

output "raw_crawl_bucket_name" {
  value = module.raw_crawl_bucket.bucket_id
}

output "crawl_queue_url" {
  value = module.crawl_queue.queue_id
}

output "raw_fetched_queue_url" {
  value = module.raw_fetched_queue.queue_id
}

output "crawler_lambda_function_name" {
  value = module.crawler_lambda.function_name
}

# ---------------------------------------------------------------------------
# Phase 2: parser-service infrastructure
# ---------------------------------------------------------------------------
#
# parser-service consumes `raw-fetched`, reads the same raw crawl
# bucket Phase 1 wrote, upserts into a real Postgres database, and
# publishes `record-changed` to a new `notify-queue` for
# notifier-service (Phase 5, not built here) to eventually consume.
#
# VPC-vs-RDS-Proxy: the build plan explicitly allows either ("use RDS
# Proxy so Lambda doesn't need to sit in the VPC ... otherwise put it
# in the VPC if that's simpler"). This env already provisions private
# subnets + a NAT gateway in Phase 0's network module specifically so
# resources like this can go in the VPC -- standing up RDS Proxy would
# be a second piece of infrastructure (with its own IAM auth wiring)
# purely to avoid using a NAT gateway that already exists and is
# already being paid for. So: parser Lambda goes directly in the
# private subnets, no RDS Proxy.

locals {
  parser_lambda_name = "agent-intel-dev-parser"

  parser_tags = {
    Environment = "dev"
    Service     = "parser-service"
  }
}

# --- notify-queue ---------------------------------------------------------
# One message per domain whose normalized record changed from its
# previous crawl. DLQ enabled for the same reason crawl-queue's is: one
# permanently-failing notification shouldn't block the queue forever.
module "notify_queue" {
  source = "../../modules/sqs"

  name       = "notify-queue"
  enable_dlq = true

  visibility_timeout_seconds = 60

  tags = local.parser_tags
}

# --- parser Lambda's security group ----------------------------------------
# No ingress needed (nothing calls the Lambda directly -- it's
# triggered by the SQS event source mapping); egress is required to
# reach Postgres, S3/SQS (via NAT), and Secrets Manager.
resource "aws_security_group" "parser_lambda" {
  name_prefix = "${local.parser_lambda_name}-"
  description = "parser Lambda -- outbound only (Postgres, AWS API calls via NAT)."
  vpc_id      = module.network.vpc_id

  tags = merge(local.parser_tags, {
    Name = "${local.parser_lambda_name}-sg"
  })
}

resource "aws_security_group_rule" "parser_lambda_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.parser_lambda.id
  description       = "All outbound -- Postgres (in-VPC), S3/SQS/Secrets Manager (via NAT)."
}

# --- Aurora Serverless v2 (parser-service's database) -----------------------
# Scales toward zero ACUs between crawl batches -- see modules/rds/main.tf's
# docstring for why Serverless v2 over a fixed-size instance.
module "parser_db" {
  source = "../../modules/rds"

  name       = "${local.parser_lambda_name}-db"
  vpc_id     = module.network.vpc_id
  subnet_ids = module.network.private_subnet_ids

  # Only these Lambdas' own security groups may reach Postgres.
  # api-service's Lambda (Phase 3) is a read-only consumer of the same
  # `domains` table parser-service writes -- added to this list rather
  # than standing up a second database, since there's exactly one
  # `domains` table and both services need to reach it. billing-service's
  # Lambda (Phase 4, declared further below) is added the same way --
  # it's the one service that WRITES to `api_keys` (issuance + Stripe
  # lifecycle), reusing this same cluster rather than standing up a
  # second database for one more table.
  allowed_security_group_ids = [
    aws_security_group.parser_lambda.id,
    aws_security_group.api_lambda.id,
    aws_security_group.billing_lambda.id,
  ]

  min_capacity = 0.5
  max_capacity = 2

  tags = local.parser_tags
}

# --- IAM: least-privilege parser Lambda execution role ----------------------
#
# Scoped to exactly what parser-service needs: consume raw-fetched,
# publish to notify-queue, read (only read -- crawler-service already
# owns writing to it) objects in the raw crawl bucket, read the DB
# master-user secret Aurora manages, plus the AWS-managed
# AWSLambdaVPCAccessExecutionRole (ENI create/describe/delete -- the
# standard permissions any VPC-attached Lambda needs, not specific to
# this function, hence the managed policy rather than reinventing it
# inline) and this function's own CloudWatch log group.

data "aws_iam_policy_document" "parser_lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "parser_lambda" {
  name               = "${local.parser_lambda_name}-role"
  assume_role_policy = data.aws_iam_policy_document.parser_lambda_assume_role.json

  tags = local.parser_tags
}

resource "aws_iam_role_policy_attachment" "parser_lambda_vpc_access" {
  role       = aws_iam_role.parser_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "parser_lambda_permissions" {
  statement {
    sid       = "ConsumeRawFetchedQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.raw_fetched_queue.queue_arn]
  }

  statement {
    sid       = "PublishNotifyQueue"
    actions   = ["sqs:SendMessage"]
    resources = [module.notify_queue.queue_arn]
  }

  statement {
    sid       = "ReadRawCrawlBucketObjects"
    actions   = ["s3:GetObject"]
    resources = ["${module.raw_crawl_bucket.bucket_arn}/*"]
  }

  statement {
    sid       = "ReadDbMasterUserSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.parser_db.master_user_secret_arn]
  }

  statement {
    sid     = "WriteOwnCloudWatchLogs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.parser_lambda_name}:*"
    ]
  }
}

resource "aws_iam_role_policy" "parser_lambda" {
  name   = "${local.parser_lambda_name}-policy"
  role   = aws_iam_role.parser_lambda.id
  policy = data.aws_iam_policy_document.parser_lambda_permissions.json
}

# --- Parser Lambda ----------------------------------------------------------

module "parser_lambda" {
  source = "../../modules/lambda"

  name        = local.parser_lambda_name
  description = "Normalizes raw-fetched crawl artifacts (agents.json, the Web Bot Auth JWKS directory) into the canonical `domains` row; publishes record-changed on change."
  handler     = "parser.handler.lambda_handler"
  runtime     = "python3.11"
  timeout     = 60
  memory_size = 256
  role_arn    = aws_iam_role.parser_lambda.arn

  # Same caveat as the crawler Lambda's `filename`: this deployment
  # package doesn't exist yet -- a build/CI step (not part of Phase 2)
  # produces it. This Terraform is structural, not applied.
  filename = "${path.module}/../../../../services/parser-service/dist/parser-service.zip"

  vpc_subnet_ids         = module.network.private_subnet_ids
  vpc_security_group_ids = [aws_security_group.parser_lambda.id]

  environment_variables = {
    RAW_DATA_BUCKET_NAME = module.raw_crawl_bucket.bucket_id
    NOTIFY_QUEUE_URL     = module.notify_queue.queue_id
    AWS_REGION           = var.region

    DB_HOST       = module.parser_db.cluster_endpoint
    DB_PORT       = tostring(module.parser_db.port)
    DB_NAME       = module.parser_db.database_name
    DB_USER       = module.parser_db.master_username
    DB_SECRET_ARN = module.parser_db.master_user_secret_arn
    # Deliberately no DB_PASSWORD here -- the password is read from
    # DB_SECRET_ARN at runtime (see services/parser-service/parser/db.py),
    # never stored as a plain Lambda environment variable.
    #
    # Deliberately no AWS_ENDPOINT_URL -- local-dev-only, see
    # services/parser-service/parser/config.py.
  }

  event_source_arn        = module.raw_fetched_queue.queue_arn
  event_source_batch_size = 10

  depends_on = [aws_iam_role_policy.parser_lambda, aws_iam_role_policy_attachment.parser_lambda_vpc_access]

  tags = local.parser_tags
}

output "notify_queue_url" {
  value = module.notify_queue.queue_id
}

output "parser_db_cluster_endpoint" {
  value = module.parser_db.cluster_endpoint
}

output "parser_db_secret_arn" {
  value = module.parser_db.master_user_secret_arn
}

output "parser_lambda_function_name" {
  value = module.parser_lambda.function_name
}

# ---------------------------------------------------------------------------
# Phase 3: api-service infrastructure (public API + free lookup frontend)
# ---------------------------------------------------------------------------
#
# api-service is a read-only consumer of two things Phase 1/2 already
# built: the `domains` table (module.parser_db, above) and the raw
# crawl S3 bucket (module.raw_crawl_bucket, Phase 1 above). It adds:
#   * its own Lambda (VPC-placed, same reasoning as parser-service's --
#     see Phase 2's comment on VPC-vs-RDS-Proxy above), fronted by a
#     new HTTP API (API Gateway v2).
#   * a small DynamoDB table backing its per-IP rate limiter (see
#     services/api-service/api/ratelimit.py).
#   * Amplify Hosting for apps/frontend, the public lookup page.

variable "github_access_token" {
  description = "GitHub personal access token (repo scope) for Amplify to pull github.com/sushilkadu/agent-intelligence. Required only to actually `apply` the `frontend_amplify` module -- supply out-of-band (e.g. `TF_VAR_github_access_token`, or a gitignored `*.auto.tfvars`), never hardcoded. See infra/terraform/modules/amplify/main.tf."
  type        = string
  sensitive   = true
  default     = null
}

locals {
  api_lambda_name = "agent-intel-dev-api"

  api_tags = {
    Environment = "dev"
    Service     = "api-service"
  }
}

# --- Rate-limit table (free-tier per-IP counter) ----------------------------

module "rate_limit_table" {
  source = "../../modules/dynamodb"

  name               = "${local.api_lambda_name}-rate-limit"
  hash_key           = "id" # "{ip}#{window_start}" -- see api/ratelimit.py
  ttl_attribute_name = "expires_at"

  tags = local.api_tags
}

# --- api Lambda's security group --------------------------------------------
# Same shape as parser_lambda's: no ingress (API Gateway invokes
# Lambda directly, not over the VPC network path), egress needed to
# reach Postgres, S3/DynamoDB (via NAT).
resource "aws_security_group" "api_lambda" {
  name_prefix = "${local.api_lambda_name}-"
  description = "api-service Lambda -- outbound only (Postgres, AWS API calls via NAT)."
  vpc_id      = module.network.vpc_id

  tags = merge(local.api_tags, {
    Name = "${local.api_lambda_name}-sg"
  })
}

resource "aws_security_group_rule" "api_lambda_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.api_lambda.id
  description       = "All outbound -- Postgres (in-VPC), S3/DynamoDB/Secrets Manager (via NAT)."
}

# --- IAM: least-privilege api Lambda execution role -------------------------
#
# Scoped to exactly what api-service needs: read the DB master-user
# secret (same secret parser-service's Lambda reads -- both are
# read/write to the same Postgres cluster, just with different SQL
# permissions at the DB-user level, which Terraform/IAM has no part
# in), read-only S3 access to the crawler's raw bucket (never write --
# crawler-service owns writing to it), read/write on its own rate
# limit table only (no wildcard resource ARNs anywhere here), plus the
# standard VPC-attached-Lambda managed policy and its own CloudWatch
# log group.

data "aws_iam_policy_document" "api_lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_lambda" {
  name               = "${local.api_lambda_name}-role"
  assume_role_policy = data.aws_iam_policy_document.api_lambda_assume_role.json

  tags = local.api_tags
}

resource "aws_iam_role_policy_attachment" "api_lambda_vpc_access" {
  role       = aws_iam_role.api_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "api_lambda_permissions" {
  statement {
    sid       = "ReadDbMasterUserSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.parser_db.master_user_secret_arn]
  }

  statement {
    sid       = "ReadOnlyRawCrawlBucketObjects"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [module.raw_crawl_bucket.bucket_arn, "${module.raw_crawl_bucket.bucket_arn}/*"]
  }

  statement {
    sid       = "ReadWriteRateLimitTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [module.rate_limit_table.table_arn]
  }

  statement {
    sid     = "WriteOwnCloudWatchLogs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.api_lambda_name}:*"
    ]
  }
}

resource "aws_iam_role_policy" "api_lambda" {
  name   = "${local.api_lambda_name}-policy"
  role   = aws_iam_role.api_lambda.id
  policy = data.aws_iam_policy_document.api_lambda_permissions.json
}

# --- api Lambda -------------------------------------------------------------

module "api_lambda" {
  source = "../../modules/lambda"

  name        = local.api_lambda_name
  description = "Public read-only lookup API (GET /v1/domains/{domain}, GET /v1/domains/{domain}/history) over the domains table + raw crawl S3 bucket, with a per-IP rate limiter."
  handler     = "app.handler"
  runtime     = "python3.11"
  timeout     = 30
  memory_size = 256
  role_arn    = aws_iam_role.api_lambda.arn

  # Same caveat as the crawler/parser Lambdas' `filename`: this
  # deployment package doesn't exist yet -- produced by a build/CI
  # step out of scope for this phase. This Terraform is structural,
  # not applied (no `terraform` CLI is even installed locally).
  filename = "${path.module}/../../../../services/api-service/dist/api-service.zip"

  vpc_subnet_ids         = module.network.private_subnet_ids
  vpc_security_group_ids = [aws_security_group.api_lambda.id]

  environment_variables = {
    RAW_DATA_BUCKET_NAME = module.raw_crawl_bucket.bucket_id
    RATE_LIMIT_TABLE_NAME = module.rate_limit_table.table_name
    AWS_REGION             = var.region

    DB_HOST       = module.parser_db.cluster_endpoint
    DB_PORT       = tostring(module.parser_db.port)
    DB_NAME       = module.parser_db.database_name
    DB_USER       = module.parser_db.master_username
    DB_SECRET_ARN = module.parser_db.master_user_secret_arn
    # Deliberately no DB_PASSWORD (read from DB_SECRET_ARN at runtime,
    # see services/api-service/api/db.py) and no AWS_ENDPOINT_URL
    # (local-dev-only, see services/api-service/api/config.py) -- same
    # exclusions as parser Lambda's env vars above.
  }

  # No event_source_arn -- unlike the crawler/parser Lambdas, this
  # function is invoked by API Gateway (see module.api_gateway below),
  # not an SQS event source mapping.

  depends_on = [aws_iam_role_policy.api_lambda, aws_iam_role_policy_attachment.api_lambda_vpc_access]

  tags = local.api_tags
}

# --- HTTP API (API Gateway v2) -----------------------------------------------

module "api_gateway" {
  source = "../../modules/api_gateway"

  name        = "${local.api_lambda_name}-http"
  description = "Public + paid lookup API -- domain lookup/history/bulk, API-key auth, per-tier rate limiting."

  lambda_invoke_arn    = module.api_lambda.invoke_arn
  lambda_function_name = module.api_lambda.function_name
  routes = [
    "GET /v1/domains/{domain}",
    "GET /v1/domains/{domain}/history",
    # Phase 4: paid-tier bulk lookup + key usage. Both still go through
    # this SAME HTTP API/Lambda (api-service's own FastAPI app already
    # dispatches all of these internally -- see api/routes.py) rather
    # than a second API Gateway instance; only billing-service (an
    # entirely separate service/deployable) gets its own instance below.
    "POST /v1/domains/bulk",
    "GET /v1/keys/me",
  ]

  # "*" methods was GET-only in Phase 3; POST added for the new bulk
  # endpoint. Origins stay "*" -- see services/api-service/app.py's
  # Phase 4 CORS comment for why bearer-token/API-key auth doesn't
  # carry the ambient-credential risk CORS exists to prevent, so this
  # policy is unchanged from Phase 3's reasoning, just widened to POST.
  cors_allow_methods = ["GET", "POST"]

  tags = local.api_tags
}

# ---------------------------------------------------------------------------
# Phase 4: billing-service infrastructure (Stripe Checkout/webhook + key
# issuance)
# ---------------------------------------------------------------------------
#
# billing-service is its OWN independently-deployable service per the
# architecture table (not merged into api-service's API Gateway/Lambda)
# -- own HTTP API, own Lambda, own IAM role. It reuses the same Aurora
# cluster api-service/parser-service already share (see
# `module.parser_db`'s `allowed_security_group_ids` above), since
# `api_keys` lives in that same database; it does NOT reuse api-service's
# rate-limit DynamoDB table or any of its IAM permissions -- api-service's
# existing Lambda role needs no new AWS permissions for Phase 4's
# API-key auth (it's all DB-based reads it already has access to).

locals {
  billing_lambda_name = "agent-intel-dev-billing"

  billing_tags = {
    Environment = "dev"
    Service     = "billing-service"
  }
}

# --- Stripe secrets ----------------------------------------------------------
#
# Created EMPTY (no `secret_string`) -- populated out-of-band (AWS
# console/CLI) once a real Stripe account exists, never through a
# Terraform variable/plan/state. Same reasoning the `secrets` module's
# own docstring gives for why it exists alongside the RDS module's
# AWS-managed master password: these two values have no native
# "AWS manages this for me" option the way the DB password does, but
# still shouldn't ever be plaintext in tfstate.
module "stripe_secret_key" {
  source = "../../modules/secrets"

  name        = "${local.billing_lambda_name}-stripe-secret-key"
  description = "Stripe secret key (sk_live_/sk_test_) -- populated out-of-band once a real Stripe account exists."

  tags = local.billing_tags
}

module "stripe_webhook_secret" {
  source = "../../modules/secrets"

  name        = "${local.billing_lambda_name}-stripe-webhook-secret"
  description = "Stripe webhook signing secret (whsec_...) -- populated out-of-band once a real Stripe webhook endpoint is registered."

  tags = local.billing_tags
}

# --- billing Lambda's security group ----------------------------------------
# Same shape as parser_lambda's/api_lambda's: no ingress (API Gateway
# invokes Lambda directly), egress needed to reach Postgres (in-VPC)
# and Secrets Manager/Stripe (via NAT).
resource "aws_security_group" "billing_lambda" {
  name_prefix = "${local.billing_lambda_name}-"
  description = "billing-service Lambda -- outbound only (Postgres, Secrets Manager, Stripe API via NAT)."
  vpc_id      = module.network.vpc_id

  tags = merge(local.billing_tags, {
    Name = "${local.billing_lambda_name}-sg"
  })
}

resource "aws_security_group_rule" "billing_lambda_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.billing_lambda.id
  description       = "All outbound -- Postgres (in-VPC), Secrets Manager/Stripe API (via NAT)."
}

# --- IAM: least-privilege billing Lambda execution role ---------------------
#
# Scoped to exactly what billing-service needs: read/write access to
# the DB master-user secret (same cluster api-service/parser-service
# use -- DB-user-level permissions, not IAM, govern read-only vs.
# read-write, same as those two services), read access to EXACTLY the
# two Stripe secrets above (nothing broader -- no wildcard resource
# ARNs), plus the standard VPC-attached-Lambda managed policy and its
# own CloudWatch log group.

data "aws_iam_policy_document" "billing_lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "billing_lambda" {
  name               = "${local.billing_lambda_name}-role"
  assume_role_policy = data.aws_iam_policy_document.billing_lambda_assume_role.json

  tags = local.billing_tags
}

resource "aws_iam_role_policy_attachment" "billing_lambda_vpc_access" {
  role       = aws_iam_role.billing_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "billing_lambda_permissions" {
  statement {
    sid       = "ReadDbMasterUserSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.parser_db.master_user_secret_arn]
  }

  statement {
    sid       = "ReadStripeSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.stripe_secret_key.secret_arn, module.stripe_webhook_secret.secret_arn]
  }

  statement {
    sid     = "WriteOwnCloudWatchLogs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.billing_lambda_name}:*"
    ]
  }
}

resource "aws_iam_role_policy" "billing_lambda" {
  name   = "${local.billing_lambda_name}-policy"
  role   = aws_iam_role.billing_lambda.id
  policy = data.aws_iam_policy_document.billing_lambda_permissions.json
}

# --- billing Lambda ----------------------------------------------------------

module "billing_lambda" {
  source = "../../modules/lambda"

  name        = local.billing_lambda_name
  description = "Stripe Checkout + webhook-driven API key issuance/lifecycle, and the customer billing portal."
  handler     = "app.handler"
  runtime     = "python3.11"
  timeout     = 30
  memory_size = 256
  role_arn    = aws_iam_role.billing_lambda.arn

  # Same caveat as every other service's `filename`: this deployment
  # package doesn't exist yet -- produced by a build/CI step out of
  # scope for this phase. This Terraform is structural, not applied.
  filename = "${path.module}/../../../../services/billing-service/dist/billing-service.zip"

  vpc_subnet_ids         = module.network.private_subnet_ids
  vpc_security_group_ids = [aws_security_group.billing_lambda.id]

  environment_variables = {
    AWS_REGION = var.region

    DB_HOST       = module.parser_db.cluster_endpoint
    DB_PORT       = tostring(module.parser_db.port)
    DB_NAME       = module.parser_db.database_name
    DB_USER       = module.parser_db.master_username
    DB_SECRET_ARN = module.parser_db.master_user_secret_arn

    STRIPE_SECRET_KEY_ARN     = module.stripe_secret_key.secret_arn
    STRIPE_WEBHOOK_SECRET_ARN = module.stripe_webhook_secret.secret_arn

    # Deliberately NOT set here (rather than set to ""): STRIPE_SELF_SERVE_PRICE_ID,
    # FRONTEND_BASE_URL, CORS_ALLOWED_ORIGINS. An empty-string env var
    # would still count as "set" to billing/config.py's
    # `os.environ.get(NAME, default)` calls and silently override its
    # sensible defaults with an unusable empty value -- omitting the
    # key entirely is what actually lets those Python-side defaults
    # apply until this environment has real values (a real Stripe
    # Price id, the deployed Amplify domain -- see
    # `module.frontend_amplify.default_domain` -- for FRONTEND_BASE_URL/
    # CORS_ALLOWED_ORIGINS) to set here instead.
    #
    # Deliberately no DB_PASSWORD/STRIPE_SECRET_KEY/STRIPE_WEBHOOK_SECRET
    # plaintext here -- both resolved from Secrets Manager at runtime
    # (see billing/db.py's `_resolve_password` /
    # billing/stripe_client.py's `_resolve_secret`). Deliberately no
    # AWS_ENDPOINT_URL -- local-dev-only, see billing/config.py.
  }

  depends_on = [aws_iam_role_policy.billing_lambda, aws_iam_role_policy_attachment.billing_lambda_vpc_access]

  tags = local.billing_tags
}

# --- HTTP API (API Gateway v2) -- billing-service's OWN instance ------------

module "billing_api_gateway" {
  source = "../../modules/api_gateway"

  name        = "${local.billing_lambda_name}-http"
  description = "Stripe Checkout/webhook + key retrieval/portal endpoints for billing-service."

  lambda_invoke_arn    = module.billing_lambda.invoke_arn
  lambda_function_name = module.billing_lambda.function_name
  routes = [
    "POST /v1/billing/checkout",
    "POST /v1/billing/webhook",
    "GET /v1/billing/session/{checkout_session_id}",
    "POST /v1/billing/portal",
  ]

  # Scoped to the frontend's origin (billing/config.py's own default),
  # NOT "*" -- see billing/config.py's `CORS_ALLOWED_ORIGINS` docstring
  # for why this service's CORS calculus differs from api-service's
  # permissive public GET routes. Mirrored here (same reasoning as
  # Phase 3's api_gateway module CORS config) so the policy applies
  # whether or not FastAPI's own CORSMiddleware runs.
  cors_allow_origins = ["http://localhost:3000"]
  cors_allow_methods = ["GET", "POST"]

  tags = local.billing_tags
}

output "billing_lambda_function_name" {
  value = module.billing_lambda.function_name
}

output "billing_api_gateway_endpoint" {
  value = module.billing_api_gateway.api_endpoint
}

output "stripe_secret_key_arn" {
  value = module.stripe_secret_key.secret_arn
}

output "stripe_webhook_secret_arn" {
  value = module.stripe_webhook_secret.secret_arn
}

# --- Frontend (Amplify Hosting) ---------------------------------------------
#
# Not applied without a real `github_access_token` supplied out of
# band -- see the `amplify` module's docstring and the `github_access_token`
# variable above.

module "frontend_amplify" {
  source = "../../modules/amplify"

  name                 = "agent-intel-dev-frontend"
  repository_url       = "https://github.com/sushilkadu/agent-intelligence"
  github_access_token  = var.github_access_token
  branch_name          = "main"

  environment_variables = {
    NEXT_PUBLIC_API_BASE_URL     = module.api_gateway.api_endpoint
    NEXT_PUBLIC_BILLING_BASE_URL = module.billing_api_gateway.api_endpoint
  }

  tags = {
    Environment = "dev"
    Service     = "frontend"
  }
}

output "rate_limit_table_name" {
  value = module.rate_limit_table.table_name
}

output "api_lambda_function_name" {
  value = module.api_lambda.function_name
}

output "api_gateway_endpoint" {
  value = module.api_gateway.api_endpoint
}

output "amplify_app_id" {
  value = module.frontend_amplify.app_id
}

output "amplify_default_domain" {
  value = module.frontend_amplify.default_domain
}
