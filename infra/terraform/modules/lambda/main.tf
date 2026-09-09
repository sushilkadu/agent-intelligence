# Generic Lambda-function-with-optional-SQS-trigger module. First real
# consumer: the crawler Lambda (envs/dev/main.tf), triggered by
# `crawl-queue`. Kept generic -- deployment package building/uploading
# and IAM role least-privilege policy content are both the caller's
# responsibility, not this module's, so it stays reusable for
# parser-service and others in later phases.

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

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days

  tags = local.merged_tags
}

resource "aws_lambda_function" "this" {
  function_name = var.name
  description   = var.description
  role          = var.role_arn
  handler       = var.handler
  runtime       = var.runtime
  timeout       = var.timeout
  memory_size   = var.memory_size

  filename         = var.filename
  source_code_hash = var.filename != null ? filebase64sha256(var.filename) : var.source_code_hash
  s3_bucket        = var.s3_bucket
  s3_key           = var.s3_key

  environment {
    variables = var.environment_variables
  }

  dynamic "vpc_config" {
    # Only emit the block when the caller actually wants VPC
    # placement -- an empty/absent vpc_config is how a Lambda function
    # stays outside any VPC (the crawler Lambda's case).
    for_each = var.vpc_subnet_ids != null ? [1] : []

    content {
      subnet_ids         = var.vpc_subnet_ids
      security_group_ids = var.vpc_security_group_ids
    }
  }

  depends_on = [aws_cloudwatch_log_group.this]

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}

resource "aws_lambda_event_source_mapping" "sqs" {
  count = var.event_source_arn != null ? 1 : 0

  event_source_arn = var.event_source_arn
  function_name    = aws_lambda_function.this.arn
  batch_size       = var.event_source_batch_size
  enabled          = var.event_source_enabled
}
