variable "name" {
  description = "Lambda function name."
  type        = string
}

variable "description" {
  description = "Human-readable description of the function."
  type        = string
  default     = ""
}

variable "handler" {
  description = "Entrypoint, e.g. \"crawler.handler.lambda_handler\"."
  type        = string
}

variable "runtime" {
  description = "Lambda runtime."
  type        = string
  default     = "python3.11"
}

variable "timeout" {
  description = "Function timeout in seconds."
  type        = number
  default     = 60
}

variable "memory_size" {
  description = "Function memory in MB."
  type        = number
  default     = 256
}

variable "filename" {
  description = "Path to the local deployment package zip. Mutually exclusive with s3_bucket/s3_key. Packaging (building this zip with the service's dependencies) is a build-pipeline concern, out of scope for this module."
  type        = string
  default     = null
}

variable "s3_bucket" {
  description = "S3 bucket holding the deployment package zip. Mutually exclusive with filename."
  type        = string
  default     = null
}

variable "s3_key" {
  description = "S3 key of the deployment package zip. Mutually exclusive with filename."
  type        = string
  default     = null
}

variable "source_code_hash" {
  description = "Base64-encoded SHA256 of the deployment package, so Terraform redeploys on code change. Required when using s3_bucket/s3_key (filename computes its own via filebase64sha256)."
  type        = string
  default     = null
}

variable "environment_variables" {
  description = "Environment variables for the function (e.g. RAW_DATA_BUCKET_NAME, RAW_FETCHED_QUEUE_URL). Deliberately does NOT include AWS_ENDPOINT_URL -- that's local-dev-only and must never be set on the deployed function."
  type        = map(string)
  default     = {}
}

variable "role_arn" {
  description = "ARN of the IAM role the function executes as. Least-privilege role construction (which specific queue/bucket ARNs it may touch) is the caller's responsibility -- kept out of this generic module."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for this function's log group."
  type        = number
  default     = 14
}

variable "event_source_arn" {
  description = "ARN of an SQS queue to trigger this function from. Omit (null) for no event source mapping."
  type        = string
  default     = null
}

variable "event_source_batch_size" {
  description = "Max number of SQS messages delivered per invocation. Only used when event_source_arn is set."
  type        = number
  default     = 10
}

variable "event_source_enabled" {
  description = "Whether the SQS event source mapping is enabled. Only used when event_source_arn is set."
  type        = bool
  default     = true
}

# --- Optional VPC placement --------------------------------------------------
#
# Added in Phase 2 for parser-service, which needs to reach a
# VPC-only RDS cluster and (per the network module's design) has no
# RDS Proxy in front of it -- see envs/dev/main.tf's comment on that
# tradeoff. Left null/empty by default so existing callers (the
# crawler Lambda, which needs no VPC access) are unaffected.

variable "vpc_subnet_ids" {
  description = "Subnet IDs to place this function's ENIs in (typically private subnets). Omit (null) for no VPC attachment. Must be set together with vpc_security_group_ids."
  type        = list(string)
  default     = null
}

variable "vpc_security_group_ids" {
  description = "Security group IDs to attach to this function's ENIs. Omit (null) for no VPC attachment. Must be set together with vpc_subnet_ids."
  type        = list(string)
  default     = null
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
