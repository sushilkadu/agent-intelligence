variable "name" {
  description = "Amplify app name."
  type        = string
}

variable "repository_url" {
  description = "GitHub repository URL, e.g. \"https://github.com/sushilkadu/agent-intelligence\"."
  type        = string
}

variable "github_access_token" {
  description = "GitHub personal access token (repo scope) Amplify uses to pull from `repository_url` and register its build webhook. Never hardcode this -- supply it out-of-band at apply time, e.g. via `TF_VAR_github_access_token` or a gitignored `*.auto.tfvars` file. See this module's main.tf docstring."
  type        = string
  sensitive   = true
}

variable "branch_name" {
  description = "Git branch to build/deploy."
  type        = string
  default     = "main"
}

variable "stage" {
  description = "Amplify branch stage (\"PRODUCTION\", \"BETA\", \"DEVELOPMENT\", etc.)."
  type        = string
  default     = "PRODUCTION"
}

variable "build_spec" {
  description = "Amplify build spec (amplify.yml content) for the Next.js app under apps/frontend."
  type        = string
  default     = <<-EOT
    version: 1
    applications:
      - appRoot: apps/frontend
        frontend:
          phases:
            preBuild:
              commands:
                - npm ci
            build:
              commands:
                - npm run build
          artifacts:
            baseDirectory: .next
            files:
              - '**/*'
          cache:
            paths:
              - node_modules/**/*
  EOT
}

variable "environment_variables" {
  description = "Environment variables for the Amplify build/runtime, e.g. { NEXT_PUBLIC_API_BASE_URL = \"https://api.example.com\" }."
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
