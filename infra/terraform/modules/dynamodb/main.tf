# Generic on-demand DynamoDB table module. First real consumer:
# api-service's free-tier per-IP rate-limit counter table (envs/dev/main.tf,
# Phase 3 -- see services/api-service/api/ratelimit.py for how it's
# used). Kept generic (not rate-limit-specific) so later phases can
# reuse it for other small keyed-lookup tables.
#
# On-demand (PAY_PER_REQUEST) billing rather than provisioned capacity:
# a public lookup API's traffic is bursty and hard to size capacity
# for up front, and this table's per-item cost is trivial regardless
# of request volume -- provisioning (and re-tuning) fixed
# read/write capacity for it would be pure operational overhead with
# no real cost benefit at this scale.

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

resource "aws_dynamodb_table" "this" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = var.hash_key

  attribute {
    name = var.hash_key
    type = var.hash_key_type
  }

  dynamic "ttl" {
    # Omit the block entirely when no TTL attribute is configured --
    # an absent `ttl` block is how a table has no TTL at all.
    for_each = var.ttl_attribute_name != null ? [1] : []

    content {
      attribute_name = var.ttl_attribute_name
      enabled        = true
    }
  }

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}
