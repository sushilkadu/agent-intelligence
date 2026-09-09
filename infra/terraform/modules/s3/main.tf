# Generic S3 bucket module. First real consumer: crawler-service's raw
# crawl data bucket (envs/dev/main.tf), keyed by
# `{domain}/{iso-timestamp}/{artifact}` -- versioned so nothing already
# written is ever silently lost, encrypted at rest, and fully blocked
# from any public access. Kept generic (not crawler-specific) so later
# phases can reuse it for other buckets.

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

resource "aws_s3_bucket" "this" {
  bucket        = var.name
  force_destroy = var.force_destroy

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = var.enable_versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
