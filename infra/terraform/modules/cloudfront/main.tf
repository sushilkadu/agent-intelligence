# Placeholder for the cloudfront module.
#
# Phase 0 scope was network + remote state bootstrap only. Phase 3
# (public API + free lookup frontend) deliberately leaves this module
# still empty -- not an oversight, a scope decision:
#
#   * The frontend (apps/frontend) is served by Amplify Hosting (see
#     the `amplify` module), which already fronts Next.js apps with
#     its own CDN by default. A second CDN layer in front of that
#     would be redundant infrastructure with nothing new to do.
#   * The build plan's architecture diagram also shows CloudFront in
#     front of API Gateway. api-service's `api_gateway` module (Phase
#     3) is reachable directly via its own `*.execute-api.<region>.
#     amazonaws.com` invoke URL for now, which is enough for a
#     dev-environment public API with no custom domain yet.
#     Standing up a CloudFront distribution in front of it today would
#     mean either leaving it pointed at a throwaway domain or blocking
#     on a real custom domain + ACM certificate that doesn't exist yet
#     -- both premature before there's an actual domain to serve.
#     It would also change api-service's rate limiter: see
#     services/api-service/api/ratelimit.py's docstring on why
#     `sourceIp` is correct only as long as nothing sits in front of
#     API Gateway, and what to switch to (the standard forwarded-for
#     header CloudFront sets) the moment this module is filled in.
#
# Revisit this module once a real domain + ACM certificate exist for
# either the API or the frontend.
