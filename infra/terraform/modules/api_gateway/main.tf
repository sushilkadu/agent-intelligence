# Generic HTTP API (API Gateway v2) + single-Lambda-proxy-integration
# module. First real consumer: api-service's public lookup API
# (envs/dev/main.tf, Phase 3).
#
# Deliberately HTTP API (v2), not REST API (v1): the build plan's
# architecture names "API Gateway (HTTP API)" specifically, and HTTP
# APIs are simpler/cheaper for a plain Lambda-proxy JSON API with no
# need for REST API v1-only features (usage plans/API keys, request
# validation, etc. -- see api-service's `api/ratelimit.py` docstring
# for why usage plans specifically don't fit this phase: they're a v1
# concept, and API keys are Phase 4's job anyway).
#
# One Lambda integration is shared by every route -- api-service is a
# single FastAPI app (via Mangum) that dispatches internally by path,
# so API Gateway only needs to get every route to the same function.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  merged_tags = merge(
    var.tags,
    {
      "Project"   = "agent-intelligence"
      "ManagedBy" = "terraform"
    }
  )
}

resource "aws_apigatewayv2_api" "this" {
  name          = var.name
  protocol_type = "HTTP"
  description   = var.description

  # Permissive CORS for this phase's routes -- see
  # services/api-service/app.py's CORSMiddleware comment for the full
  # rationale (public, unauthenticated, read-only GET endpoints with
  # no cookies/credentials in play). Mirrored here so the same policy
  # applies whether or not FastAPI's own CORSMiddleware runs (API
  # Gateway can short-circuit OPTIONS preflights before they ever
  # reach the Lambda).
  cors_configuration {
    allow_origins = var.cors_allow_origins
    allow_methods = var.cors_allow_methods
    allow_headers = var.cors_allow_headers
  }

  tags = merge(local.merged_tags, {
    Name = var.name
  })
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = var.lambda_invoke_arn
  payload_format_version = "2.0"
}

# One route per `var.routes` entry, e.g. "GET /v1/domains/{domain}" --
# all routed to the same Lambda integration (see module docstring).
resource "aws_apigatewayv2_route" "this" {
  for_each = toset(var.routes)

  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = var.stage_name
  auto_deploy = true

  # Account/stage-level throttling as a blunt, cheap-to-configure
  # safety net in front of the Lambda -- api-service's own per-IP
  # DynamoDB rate limiter (api/ratelimit.py) is the real free-tier
  # enforcement; this is just a backstop against a burst overwhelming
  # the Lambda/DB regardless of source.
  default_route_settings {
    throttling_burst_limit = var.throttling_burst_limit
    throttling_rate_limit  = var.throttling_rate_limit
  }

  tags = merge(local.merged_tags, {
    Name = "${var.name}-${var.stage_name}"
  })
}

resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  # Scoped to this specific API's execution ARN (any stage/route under
  # it), not a wildcard across all API Gateway APIs in the account.
  source_arn = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
