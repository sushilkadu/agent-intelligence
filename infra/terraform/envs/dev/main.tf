# Dev environment root module.
#
# Phase 0 scope: wires up the remote state backend (created by
# ../backend-bootstrap) and calls the network module. Nothing else is
# provisioned here yet -- RDS/SQS/Lambda/API Gateway/etc. modules exist as
# empty placeholders under ../../modules and are not called from any env
# yet.
#
# NOT applied as part of Phase 0. Do not `terraform init`/`plan`/`apply`
# this against real AWS.

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
