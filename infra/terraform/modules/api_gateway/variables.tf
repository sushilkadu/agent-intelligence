variable "name" {
  description = "HTTP API name."
  type        = string
}

variable "description" {
  description = "Human-readable description of the API."
  type        = string
  default     = ""
}

variable "lambda_invoke_arn" {
  description = "The backing Lambda function's `invoke_arn` (see the `lambda` module's `invoke_arn` output) -- every route in `routes` proxies to this one function."
  type        = string
}

variable "lambda_function_name" {
  description = "The backing Lambda function's name, for the `aws_lambda_permission` that lets this API invoke it."
  type        = string
}

variable "routes" {
  description = "Route keys to create, each proxied to the same Lambda integration, e.g. [\"GET /v1/domains/{domain}\", \"GET /v1/domains/{domain}/history\"]."
  type        = list(string)
}

variable "stage_name" {
  description = "Stage name. \"$default\" serves at the API's root (no stage path segment) -- the common choice for a single-stage HTTP API."
  type        = string
  default     = "$default"
}

variable "throttling_rate_limit" {
  description = "Stage-level steady-state request rate limit (requests/second) -- a coarse backstop in front of the Lambda; api-service's own per-IP DynamoDB limiter is the real free-tier enforcement (see api-service's api/ratelimit.py)."
  type        = number
  default     = 100
}

variable "throttling_burst_limit" {
  description = "Stage-level burst request limit."
  type        = number
  default     = 50
}

variable "cors_allow_origins" {
  description = "CORS allowed origins. \"*\" is intentional for this phase's public, unauthenticated, read-only GET routes -- see services/api-service/app.py's CORSMiddleware comment. Tighten this (real origin allowlist) before adding authenticated/paid routes to this API."
  type        = list(string)
  default     = ["*"]
}

variable "cors_allow_methods" {
  description = "CORS allowed methods."
  type        = list(string)
  default     = ["GET"]
}

variable "cors_allow_headers" {
  description = "CORS allowed request headers."
  type        = list(string)
  default     = ["*"]
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
