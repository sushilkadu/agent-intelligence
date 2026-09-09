"""Async HTTP fetch logic for agent-identity signals.

Phase 1 scope: fetch raw bytes only. No JSON parsing/validation, no
normalization -- that's Phase 2 (parser-service). A response is
"present" if the server answered with HTTP 200; anything else
(404, 5xx, timeout, DNS/connection failure) is treated as "not
present", which is the expected, common case for most domains and must
never raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .config import (
    AGENTS_JSON_PATH,
    DEFAULT_TIMEOUT_SECONDS,
    URL_SCHEME,
    WEB_BOT_AUTH_WELL_KNOWN_PATH,
)


@dataclass
class FetchResult:
    """The outcome of fetching a single URL.

    `present` is True only for a clean HTTP 200. Malformed content
    (e.g. invalid JSON in a 200 response) is still "present" at this
    layer -- Phase 1 stores raw bytes as-is and leaves interpreting
    them to parser-service. `error` is set for network-level failures
    (timeout, connection refused, DNS failure) and is mutually
    exclusive with a populated `status_code`.
    """

    url: str
    fetched_at: str
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    present: bool = False
    error: str | None = None


@dataclass
class CrawlResult:
    """Both signals fetched for one domain."""

    domain: str
    agents_json: FetchResult
    web_bot_auth: FetchResult


def build_url(domain: str, path: str) -> str:
    """Build a fetch URL for `domain` + well-known `path`.

    The scheme is overridable via `CRAWLER_URL_SCHEME` (see
    config.py) so local/dev tooling can point this at a plain-HTTP
    mock server without touching the crawl logic itself.
    """
    return f"{URL_SCHEME}://{domain}{path}"


async def fetch_url(client: httpx.AsyncClient, url: str) -> FetchResult:
    """Fetch `url`, translating expected network failures into a
    `FetchResult` instead of letting them propagate. A domain that
    doesn't resolve, doesn't respond, or times out is the normal case
    for most of the crawl universe, not an exceptional one.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        response = await client.get(url, timeout=DEFAULT_TIMEOUT_SECONDS)
    except httpx.TimeoutException as exc:
        return FetchResult(url=url, fetched_at=fetched_at, error=f"timeout: {exc}")
    except httpx.ConnectError as exc:
        # Covers both DNS resolution failures and connection refused.
        return FetchResult(url=url, fetched_at=fetched_at, error=f"connect_error: {exc}")
    except httpx.RequestError as exc:
        # Catch-all for any other transport-level failure (e.g. TLS
        # errors, too many redirects) so a single bad domain never
        # takes down the rest of a crawl batch.
        return FetchResult(url=url, fetched_at=fetched_at, error=f"request_error: {exc}")

    return FetchResult(
        url=url,
        fetched_at=fetched_at,
        status_code=response.status_code,
        headers=dict(response.headers),
        body=response.content,
        present=response.status_code == 200,
    )


async def fetch_agents_json(client: httpx.AsyncClient, domain: str) -> FetchResult:
    return await fetch_url(client, build_url(domain, AGENTS_JSON_PATH))


async def fetch_web_bot_auth_directory(client: httpx.AsyncClient, domain: str) -> FetchResult:
    return await fetch_url(client, build_url(domain, WEB_BOT_AUTH_WELL_KNOWN_PATH))


async def crawl_domain(domain: str, client: httpx.AsyncClient | None = None) -> CrawlResult:
    """Fetch both signals for `domain`.

    Pass an existing `client` to reuse connection pooling across many
    domains in one crawl batch (the Lambda handler does this); omit it
    for one-off/manual use, which opens and closes a client for you.
    """
    if client is not None:
        agents_json = await fetch_agents_json(client, domain)
        web_bot_auth = await fetch_web_bot_auth_directory(client, domain)
        return CrawlResult(domain=domain, agents_json=agents_json, web_bot_auth=web_bot_auth)

    async with httpx.AsyncClient(follow_redirects=True) as new_client:
        agents_json = await fetch_agents_json(new_client, domain)
        web_bot_auth = await fetch_web_bot_auth_directory(new_client, domain)
        return CrawlResult(domain=domain, agents_json=agents_json, web_bot_auth=web_bot_auth)
