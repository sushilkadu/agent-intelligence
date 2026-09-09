output "queue_id" {
  description = "The queue's URL (its Terraform \"id\")."
  value       = aws_sqs_queue.this.id
}

output "queue_arn" {
  description = "The queue's ARN."
  value       = aws_sqs_queue.this.arn
}

output "dlq_arn" {
  description = "The dead-letter queue's ARN, if enabled (null otherwise)."
  value       = var.enable_dlq ? aws_sqs_queue.dlq[0].arn : null
}
