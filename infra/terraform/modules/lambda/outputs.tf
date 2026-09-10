output "function_name" {
  description = "The Lambda function's name."
  value       = aws_lambda_function.this.function_name
}

output "function_arn" {
  description = "The Lambda function's ARN."
  value       = aws_lambda_function.this.arn
}

output "invoke_arn" {
  description = "The function's API-Gateway-invoke-formatted ARN (`aws_lambda_function.this.invoke_arn`). Only needed by callers wiring this function up behind an API Gateway integration (e.g. the `api_gateway` module) -- added in Phase 3 for api-service, additive/unused by existing SQS-triggered consumers (crawler/parser Lambdas)."
  value       = aws_lambda_function.this.invoke_arn
}

output "log_group_name" {
  description = "Name of the function's CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.name
}

output "log_group_arn" {
  description = "ARN of the function's CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.arn
}
