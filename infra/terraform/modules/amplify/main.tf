# Amplify Hosting for apps/frontend (the Next.js public lookup page).
# First real consumer: envs/dev/main.tf, Phase 3.
#
# Amplify (not a hand-rolled S3+CloudFront static site) because
# apps/frontend is a real Next.js App Router app -- Amplify Hosting's
# "WEB_COMPUTE" platform builds and serves Next.js's SSR/server
# components out of the box, including its own CDN in front, without
# this repo needing to stand up and maintain a separate CloudFront
# distribution for the frontend (see the `cloudfront` module's
# docstring for why that's left as a placeholder this phase).
#
# --- The GitHub access token ------------------------------------------------
#
# `aws_amplify_app.access_token` is how Amplify authenticates to pull
# from a GitHub repo for its build webhook. This MUST be a real
# personal access token (repo scope) at apply time -- there is no way
# to make Amplify's GitHub integration work without one. This module
# takes it as a `sensitive = true` Terraform variable
# (`github_access_token`) with no default, so:
#   * it is never hardcoded anywhere in this repo or Terraform state's
#     plan output (sensitive variables are redacted from CLI output,
#     though they still land in state itself -- see AWS/Amplify's own
#     docs on this; treat `terraform.tfstate` for this module as
#     sensitive).
#   * a real value has to be supplied out-of-band before this module
#     can ever actually be applied -- e.g.
#     `TF_VAR_github_access_token=... terraform apply`, or a
#     `*.auto.tfvars` file that is gitignored (never commit one).
#   * this module is structurally complete but not expected to be
#     usable without that real secret -- that's expected/fine for
#     Phase 3 (see the Phase 3 report: no `terraform apply` was run
#     against real AWS, and `terraform` isn't even installed locally).

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

resource "aws_amplify_app" "this" {
  name       = var.name
  repository = var.repository_url

  access_token = var.github_access_token

  platform = "WEB_COMPUTE"

  # Monorepo build: apps/frontend is one package among several in this
  # repo, so the build spec sets appRoot rather than assuming the repo
  # root is the Next.js app.
  build_spec = var.build_spec

  environment_variables = var.environment_variables

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}

resource "aws_amplify_branch" "this" {
  app_id      = aws_amplify_app.this.id
  branch_name = var.branch_name
  framework   = "Next.js - SSR"
  stage       = var.stage

  enable_auto_build = true

  tags = merge(local.merged_tags, {
    Name = "${var.name}-${var.branch_name}"
  })
}
