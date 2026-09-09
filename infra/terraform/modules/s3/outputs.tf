output "bucket_id" {
  description = "The bucket's name/id."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "The bucket's ARN."
  value       = aws_s3_bucket.this.arn
}
