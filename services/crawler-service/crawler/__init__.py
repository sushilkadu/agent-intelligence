"""crawler: Phase 1 crawl logic for agent-identity signals.

Fetches `agents.json` and the Web Bot Auth signature-agent-card JWKS
directory for a domain, stores the raw results in S3, and publishes a
`raw-fetched` SQS event. Triggered in production by an SQS
(`crawl-queue`) -> Lambda event source mapping (see `crawler.handler`).
"""

from .fetch import CrawlResult, FetchResult, crawl_domain
from .handler import lambda_handler

__all__ = ["CrawlResult", "FetchResult", "crawl_domain", "lambda_handler"]
