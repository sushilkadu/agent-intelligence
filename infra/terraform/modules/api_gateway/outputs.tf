output "api_id" {
  description = "The HTTP API's id."
  value       = aws_apigatewayv2_api.this.id
}

output "api_endpoint" {
  description = "The invoke URL for this API's stage."
  value       = aws_apigatewayv2_stage.this.invoke_url
}

output "execution_arn" {
  description = "The API's execution ARN (for additional `aws_lambda_permission` resources, if ever needed)."
  value       = aws_apigatewayv2_api.this.execution_arn
}
