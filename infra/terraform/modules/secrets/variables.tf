variable "name" {
  description = "Secret name (e.g. \"agent-intel-dev-notifier-webhook-signing-key\")."
  type        = string
}

variable "description" {
  description = "Human-readable description of the secret's purpose."
  type        = string
  default     = ""
}

variable "secret_string" {
  description = "Initial secret value. Omit (null) to create an empty secret and populate its value out-of-band instead of passing it through a Terraform variable."
  type        = string
  default     = null
  sensitive   = true
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
