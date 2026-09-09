variable "name" {
  description = "Name prefix applied to all resources created by this module (e.g. \"agent-intel-dev-parser-db\"). Used as the cluster identifier."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID to create the cluster's security group in."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for the DB subnet group -- typically the network module's private_subnet_ids. Must span at least 2 AZs (an RDS requirement)."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security group IDs allowed to reach Postgres (5432) on this cluster -- e.g. the parser Lambda's security group. No CIDR-based ingress is created; only these SGs can connect."
  type        = list(string)
  default     = []
}

variable "engine_version" {
  description = "Aurora PostgreSQL engine version. Must be a version that supports Serverless v2 (Aurora PostgreSQL >= 13.6/14.3/15.2 families)."
  type        = string
  default     = "15.4"
}

variable "database_name" {
  description = "Initial database name created in the cluster."
  type        = string
  default     = "agent_intel"
}

variable "master_username" {
  description = "Master username. The password is never set here -- see manage_master_user_password in main.tf."
  type        = string
  default     = "agent_intel"
}

variable "min_capacity" {
  description = "Minimum Aurora Capacity Units (ACUs) the Serverless v2 cluster scales down to. 0.5 is the lowest AWS allows -- lets the cluster sit near-idle (and near-zero cost) between crawl batches."
  type        = number
  default     = 0.5
}

variable "max_capacity" {
  description = "Maximum ACUs the cluster scales up to under load."
  type        = number
  default     = 2
}

variable "skip_final_snapshot" {
  description = "If true, no final snapshot is taken on cluster deletion. true is appropriate for dev (fast, disposable teardown); should be false for a prod environment."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "If true, the cluster cannot be deleted via Terraform/the API without first disabling this. false for dev, should be true for prod."
  type        = bool
  default     = false
}

variable "apply_immediately" {
  description = "If true, modifications are applied immediately rather than during the next maintenance window. true is convenient for dev iteration; should generally be false for prod to avoid unexpected downtime."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
