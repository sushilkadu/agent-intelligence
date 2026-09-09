variable "name" {
  description = "Bucket name (must be globally unique)."
  type        = string
}

variable "enable_versioning" {
  description = "Enable S3 object versioning (protects raw crawl artifacts from accidental overwrite)."
  type        = bool
  default     = true
}

variable "force_destroy" {
  description = "Allow `terraform destroy` to delete this bucket even if it still contains objects. Keep false outside of throwaway/dev environments."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
