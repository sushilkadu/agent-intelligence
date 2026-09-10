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
    CRAWL_LLMS_TXT_ENABLED,
    DEFAULT_TIMEOUT_SECONDS,
    LLMS_TXT_PATH,
    URL_SCHEME,
    WEB_BOT_AUTH_WELL_KNOWN_PATH,
)
from .onchain import lookup_on_chain_ref


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
    """Every signal fetched/looked-up for one domain.

    `llms_txt` is a real `FetchResult` only when `CRAWL_LLMS_TXT_ENABLED`
    is true (see config.py); disabled, it's a not-present placeholder
    with no network call made -- mirrors how `on_chain_ref` is always
    present on the result but only ever non-None once a real on-chain
    backend exists (see onchain.py).
    """

    domain: str
    agents_json: FetchResult
    web_bot_auth: FetchResult
    llms_txt: FetchResult
    on_chain_ref: str | None = None


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


async def fetch_llms_txt(client: httpx.AsyncClient, domain: str) -> FetchResult:
    """Fetch `domain`'s llms.txt, mirroring `fetch_agents_json` exactly
    (same well-known-path-fetch pattern, same "absent is normal, never
    raises" semantics). Only called when `CRAWL_LLMS_TXT_ENABLED` is
    true -- see `crawl_domain`.
    """
    return await fetch_url(client, build_url(domain, LLMS_TXT_PATH))


def _not_fetched_result(url: str) -> FetchResult:
    """A `FetchResult` placeholder for a signal this crawl deliberately
    did not attempt (llms.txt fetching disabled via feature flag) --
    distinct from a real fetch that came back absent (404/timeout/etc.,
    which also has `present=False` but a populated `fetched_at`/`error`).
    """
    return FetchResult(url=url, fetched_at=datetime.now(timezone.utc).isoformat(), present=False)


async def crawl_domain(domain: str, client: httpx.AsyncClient | None = None) -> CrawlResult:
    """Fetch every signal for `domain`: agents.json and the Web Bot Auth
    JWKS directory always; llms.txt only when `CRAWL_LLMS_TXT_ENABLED`
    is true; the on-chain registry ref via `onchain.lookup_on_chain_ref`
    (itself feature-flagged and, today, always `None` -- see
    onchain.py's docstring).

    Pass an existing `client` to reuse connection pooling across many
    domains in one crawl batch (the Lambda handler does this); omit it
    for one-off/manual use, which opens and closes a client for you.
    """
    async def _run(active_client: httpx.AsyncClient) -> CrawlResult:
        agents_json = await fetch_agents_json(active_client, domain)
        web_bot_auth = await fetch_web_bot_auth_directory(active_client, domain)
        llms_txt = (
            await fetch_llms_txt(active_client, domain)
            if CRAWL_LLMS_TXT_ENABLED
            else _not_fetched_result(build_url(domain, LLMS_TXT_PATH))
        )
        return CrawlResult(
            domain=domain,
            agents_json=agents_json,
            web_bot_auth=web_bot_auth,
            llms_txt=llms_txt,
            on_chain_ref=lookup_on_chain_ref(domain),
        )

    if client is not None:
        return await _run(client)

    async with httpx.AsyncClient(follow_redirects=True) as new_client:
        return await _run(new_client)
