output "table_name" {
  description = "The table's name/id."
  value       = aws_dynamodb_table.this.name
}

output "table_arn" {
  description = "The table's ARN."
  value       = aws_dynamodb_table.this.arn
}
