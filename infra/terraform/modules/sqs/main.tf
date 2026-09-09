# Generic SQS queue module. First real consumers: crawler-service's
# `crawl-queue` (domains awaiting crawl, triggers the crawler Lambda)
# and `raw-fetched` (published once a domain's crawl completes,
# consumed by parser-service in Phase 2) -- both wired up in
# envs/dev/main.tf. Kept generic so later phases can reuse it.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  merged_tags = merge(
    var.tags,
    {
      "Project"   = "agent-intelligence"
      "ManagedBy" = "terraform"
    }
  )
}

resource "aws_sqs_queue" "dlq" {
  count = var.enable_dlq ? 1 : 0

  name                      = "${var.name}-dlq"
  message_retention_seconds = var.message_retention_seconds

  tags = merge(local.merged_tags, {
    Name = "${var.name}-dlq"
  })
}

resource "aws_sqs_queue" "this" {
  name                       = var.name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds

  redrive_policy = var.enable_dlq ? jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[0].arn
    maxReceiveCount     = var.max_receive_count
  }) : null

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}
