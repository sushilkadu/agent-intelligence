# Generic Secrets Manager secret module.
#
# Phase 2 note: this module is fleshed out (no longer a Phase 0
# placeholder) but is NOT called anywhere in envs/dev/main.tf yet. The
# one secret this phase actually needs -- parser-service's DB
# credential -- is deliberately handled a different way: the `rds`
# module sets `manage_master_user_password = true`, which makes AWS
# create and manage that secret itself. That's strictly better for a
# DB master password than round-tripping it through this module, which
# would require the plaintext password to pass through a Terraform
# variable (and therefore risk ending up in tfstate/plan output) at
# some point.
#
# This module exists for the secrets that DON'T have a native
# "AWS manages this for me" option -- e.g. a webhook-signing secret for
# notifier-service (Phase 5), or a third-party API key. Kept generic
# (like the s3/sqs modules) so a later phase can call it without
# writing this boilerplate again.

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

resource "aws_secretsmanager_secret" "this" {
  name        = var.name
  description = var.description

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}

resource "aws_secretsmanager_secret_version" "this" {
  # Only create a version if an initial value was actually supplied --
  # a caller may prefer to populate the secret out-of-band (e.g. via
  # the AWS console/CLI) rather than pass a sensitive value through a
  # Terraform variable.
  count = var.secret_string != null ? 1 : 0

  secret_id     = aws_secretsmanager_secret.this.id
  secret_string = var.secret_string
}
