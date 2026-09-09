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
