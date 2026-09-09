output "function_name" {
  description = "The Lambda function's name."
  value       = aws_lambda_function.this.function_name
}

output "function_arn" {
  description = "The Lambda function's ARN."
  value       = aws_lambda_function.this.arn
}

output "log_group_name" {
  description = "Name of the function's CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.name
}

output "log_group_arn" {
  description = "ARN of the function's CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.arn
}
