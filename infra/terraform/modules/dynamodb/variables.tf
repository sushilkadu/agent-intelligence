variable "name" {
  description = "Table name."
  type        = string
}

variable "hash_key" {
  description = "Partition key attribute name."
  type        = string
  default     = "id"
}

variable "hash_key_type" {
  description = "Partition key attribute type: \"S\" (string), \"N\" (number), or \"B\" (binary)."
  type        = string
  default     = "S"
}

variable "ttl_attribute_name" {
  description = "Attribute name DynamoDB uses to auto-expire items (e.g. \"expires_at\"). Omit (null) to disable TTL."
  type        = string
  default     = null
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
