# Aurora Serverless v2 (Postgres-compatible) module.
#
# Phase 2: first real consumer is parser-service's database (envs/dev/
# main.tf's `module.parser_db`). Aurora Serverless v2 over a
# fixed-size RDS instance is a deliberate choice, per the build plan's
# own guidance for this stack: it scales toward zero ACUs between
# crawl cycles (crawls happen in bursts, not continuously), which
# matters more for dev/staging cost than anything else here. A fixed
# db.t3.* instance would sit fully provisioned (and billed) 24/7 even
# though parser-service only touches the DB for the few minutes a
# batch of raw-fetched messages is being processed.
#
# Credentials: uses RDS's native `manage_master_user_password` (AWS
# creates and rotates a Secrets Manager secret itself) rather than a
# Terraform-managed secret -- see this module's outputs.tf and
# envs/dev/main.tf's comment on why that's preferred over wiring up
# infra/terraform/modules/secrets for this specific credential (the
# password is never handled by Terraform state, a human, or this
# module at all).

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

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-subnet-group"
  subnet_ids = var.subnet_ids

  tags = merge(local.merged_tags, {
    Name = "${var.name}-subnet-group"
  })
}

# Postgres access is allowed ONLY from security groups explicitly
# passed in via `allowed_security_group_ids` (parser-service's Lambda
# SG in practice) -- no CIDR-based ingress rule, so nothing can reach
# this cluster just by being in the same VPC.
resource "aws_security_group" "this" {
  name_prefix = "${var.name}-"
  description = "Aurora Postgres for ${var.name} -- inbound 5432 only from explicitly allowed security groups."
  vpc_id      = var.vpc_id

  tags = merge(local.merged_tags, {
    Name = "${var.name}-sg"
  })
}

resource "aws_security_group_rule" "postgres_ingress" {
  count = length(var.allowed_security_group_ids)

  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.this.id
  source_security_group_id = var.allowed_security_group_ids[count.index]
  description              = "Postgres from ${var.allowed_security_group_ids[count.index]}"
}

resource "aws_security_group_rule" "egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.this.id
  description       = "Aurora's own outbound (patching, etc.) -- the cluster never needs to be reached from outside its SG-scoped ingress rule above."
}

resource "aws_rds_cluster" "this" {
  cluster_identifier = var.name
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # required value for Aurora Serverless v2
  engine_version     = var.engine_version
  database_name      = var.database_name
  master_username    = var.master_username

  # AWS creates and manages a Secrets Manager secret for the master
  # password -- Terraform never sees, stores, or diffs the password
  # itself (avoiding it ending up in tfstate in any form).
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  storage_encrypted      = true
  skip_final_snapshot    = var.skip_final_snapshot
  deletion_protection    = var.deletion_protection
  apply_immediately      = var.apply_immediately

  serverlessv2_scaling_configuration {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}

resource "aws_rds_cluster_instance" "this" {
  cluster_identifier = aws_rds_cluster.this.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version

  tags = merge(local.merged_tags, {
    Name = "${var.name}-instance-1"
  })
}
