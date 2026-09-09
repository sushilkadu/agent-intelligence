output "cluster_endpoint" {
  description = "The cluster's writer endpoint (host only, no port)."
  value       = aws_rds_cluster.this.endpoint
}

output "reader_endpoint" {
  description = "The cluster's reader endpoint."
  value       = aws_rds_cluster.this.reader_endpoint
}

output "port" {
  description = "The port Postgres is listening on."
  value       = aws_rds_cluster.this.port
}

output "database_name" {
  description = "The initial database name."
  value       = aws_rds_cluster.this.database_name
}

output "master_username" {
  description = "The master username (not a secret by itself -- the password is managed separately, see master_user_secret_arn)."
  value       = aws_rds_cluster.this.master_username
}

output "master_user_secret_arn" {
  description = "ARN of the Secrets Manager secret AWS created for the master user's password (manage_master_user_password = true). Consumers (e.g. the parser Lambda's IAM role/env vars) read the password from this secret at runtime -- Terraform itself never handles the plaintext password."
  value       = aws_rds_cluster.this.master_user_secret[0].secret_arn
}

output "security_group_id" {
  description = "ID of the security group guarding this cluster's Postgres port."
  value       = aws_security_group.this.id
}

output "cluster_arn" {
  description = "ARN of the Aurora cluster."
  value       = aws_rds_cluster.this.arn
}
