output "app_id" {
  description = "The Amplify app's id."
  value       = aws_amplify_app.this.id
}

output "default_domain" {
  description = "The app's default Amplify-generated domain (e.g. \"<app_id>.amplifyapp.com\")."
  value       = aws_amplify_app.this.default_domain
}

output "branch_name" {
  description = "The deployed branch name."
  value       = aws_amplify_branch.this.branch_name
}
