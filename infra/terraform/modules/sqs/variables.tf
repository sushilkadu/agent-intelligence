variable "name" {
  description = "Queue name."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a message is invisible to other consumers after being received. Should be >= the consumer's (Lambda's) max processing time."
  type        = number
  default     = 30
}

variable "message_retention_seconds" {
  description = "How long an unconsumed message is retained before being dropped."
  type        = number
  default     = 345600 # 4 days
}

variable "enable_dlq" {
  description = "If true, create a companion dead-letter queue and redrive messages to it after `max_receive_count` failed deliveries -- keeps one poison message (e.g. a domain whose crawl always errors) from blocking the queue forever."
  type        = bool
  default     = false
}

variable "max_receive_count" {
  description = "Number of delivery attempts before a message is moved to the dead-letter queue. Only used when `enable_dlq` is true."
  type        = number
  default     = 5
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
