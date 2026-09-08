# Bootstrap Terraform config for the dev environment's remote state.
#
# This config has NO remote backend itself (chicken-and-egg problem: you
# can't point at a remote state bucket that doesn't exist yet). Run this
# once with local state to create the S3 bucket + DynamoDB lock table,
# then every other env root module (e.g. ../main.tf) points its backend
# block at the resources created here.
#
# Phase 0: files only, not applied. Do not `terraform apply` this.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  description = "AWS region for the state bucket + lock table."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Globally-unique S3 bucket name for Terraform remote state."
  type        = string
  default     = "agent-intelligence-tfstate-dev"
}

variable "lock_table_name" {
  description = "DynamoDB table name used for Terraform state locking."
  type        = string
  default     = "agent-intelligence-tfstate-lock-dev"
}

resource "aws_s3_bucket" "tfstate" {
  bucket = var.state_bucket_name

  # Remote state buckets should never be deleted out from under active
  # environments by an errant `terraform destroy`.
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project   = "agent-intelligence"
    Purpose   = "terraform-remote-state"
    ManagedBy = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tfstate_lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Project   = "agent-intelligence"
    Purpose   = "terraform-remote-state-lock"
    ManagedBy = "terraform"
  }
}

output "state_bucket_name" {
  value = aws_s3_bucket.tfstate.bucket
}

output "lock_table_name" {
  value = aws_dynamodb_table.tfstate_lock.name
}
