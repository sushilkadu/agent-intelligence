"""Unit tests for crawler.fetch -- all HTTP mocked via respx, no real network."""

from __future__ import annotations

import asyncio
import time

import httpx
import respx

from crawler.config import AGENTS_JSON_PATH, LLMS_TXT_PATH, WEB_BOT_AUTH_WELL_KNOWN_PATH
from crawler.fetch import (
    build_url,
    crawl_domain,
    fetch_agents_json,
    fetch_llms_txt,
    fetch_web_bot_auth_directory,
)

DOMAIN = "example.com"
AGENTS_JSON_URL = build_url(DOMAIN, AGENTS_JSON_PATH)
WEB_BOT_AUTH_URL = build_url(DOMAIN, WEB_BOT_AUTH_WELL_KNOWN_PATH)
LLMS_TXT_URL = build_url(DOMAIN, LLMS_TXT_PATH)


def run(coro):
    return asyncio.run(coro)


def test_build_url_uses_configured_path_constant():
    assert AGENTS_JSON_URL == "https://example.com/agents.json"
    assert WEB_BOT_AUTH_URL == "https://example.com/.well-known/http-message-signatures-directory"


# --- agents.json ---------------------------------------------------------


@respx.mock
def test_agents_json_present_valid_json():
    respx.get(AGENTS_JSON_URL).mock(
        return_value=httpx.Response(200, json={"agents": [{"name": "demo-bot"}]})
    )

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_agents_json(client, DOMAIN)

    result = run(go())

    assert result.present is True
    assert result.status_code == 200
    assert result.error is None
    assert b"demo-bot" in result.body


@respx.mock
def test_agents_json_missing_404():
    respx.get(AGENTS_JSON_URL).mock(return_value=httpx.Response(404))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_agents_json(client, DOMAIN)

    result = run(go())

    assert result.present is False
    assert result.status_code == 404
    assert result.error is None


@respx.mock
def test_agents_json_malformed_json_is_still_captured_raw():
    # Phase 1 does not parse/validate content -- a 200 with garbage
    # body must still be captured as "present" and stored as-is;
    # interpreting it is parser-service's job (Phase 2).
    respx.get(AGENTS_JSON_URL).mock(
        return_value=httpx.Response(200, content=b"{not valid json::", headers={"content-type": "application/json"})
    )

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_agents_json(client, DOMAIN)

    result = run(go())

    assert result.present is True
    assert result.status_code == 200
    assert result.body == b"{not valid json::"


@respx.mock
def test_agents_json_timeout():
    respx.get(AGENTS_JSON_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_agents_json(client, DOMAIN)

    result = run(go())

    assert result.present is False
    assert result.status_code is None
    assert result.error is not None
    assert "timeout" in result.error


@respx.mock
def test_agents_json_connection_error_dns_or_refused():
    respx.get(AGENTS_JSON_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_agents_json(client, DOMAIN)

    result = run(go())

    assert result.present is False
    assert result.status_code is None
    assert result.error is not None
    assert "connect_error" in result.error


# --- Web Bot Auth JWKS directory ------------------------------------------


@respx.mock
def test_web_bot_auth_present_valid_jwks():
    respx.get(WEB_BOT_AUTH_URL).mock(return_value=httpx.Response(200, json={"keys": [{"kty": "OKP", "kid": "k1"}]}))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_web_bot_auth_directory(client, DOMAIN)

    result = run(go())

    assert result.present is True
    assert result.status_code == 200
    assert b"OKP" in result.body


@respx.mock
def test_web_bot_auth_missing_404():
    respx.get(WEB_BOT_AUTH_URL).mock(return_value=httpx.Response(404))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_web_bot_auth_directory(client, DOMAIN)

    result = run(go())

    assert result.present is False
    assert result.status_code == 404


@respx.mock
def test_web_bot_auth_malformed_jwks_is_still_captured_raw():
    respx.get(WEB_BOT_AUTH_URL).mock(return_value=httpx.Response(200, content=b"not-a-jwks-at-all"))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_web_bot_auth_directory(client, DOMAIN)

    result = run(go())

    assert result.present is True
    assert result.body == b"not-a-jwks-at-all"


@respx.mock
def test_web_bot_auth_timeout():
    respx.get(WEB_BOT_AUTH_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_web_bot_auth_directory(client, DOMAIN)

    result = run(go())

    assert result.present is False
    assert result.error is not None


# --- llms.txt ---------------------------------------------------------


@respx.mock
def test_llms_txt_present_valid_text():
    respx.get(LLMS_TXT_URL).mock(return_value=httpx.Response(200, content=b"# example.com\n\nA demo site.\n"))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_llms_txt(client, DOMAIN)

    result = run(go())

    assert result.present is True
    assert result.status_code == 200
    assert result.error is None
    assert b"example.com" in result.body


@respx.mock
def test_llms_txt_missing_404():
    respx.get(LLMS_TXT_URL).mock(return_value=httpx.Response(404))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_llms_txt(client, DOMAIN)

    result = run(go())

    assert result.present is False
    assert result.status_code == 404
    assert result.error is None


@respx.mock
def test_llms_txt_timeout():
    respx.get(LLMS_TXT_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    async def go():
        async with httpx.AsyncClient() as client:
            return await fetch_llms_txt(client, DOMAIN)

    result = run(go())

    assert result.present is False
    assert result.error is not None
    assert "timeout" in result.error


# --- combined crawl_domain -------------------------------------------------


@respx.mock
def test_crawl_domain_fetches_all_signals_when_llms_txt_enabled():
    respx.get(AGENTS_JSON_URL).mock(return_value=httpx.Response(200, json={"agents": []}))
    respx.get(WEB_BOT_AUTH_URL).mock(return_value=httpx.Response(404))
    respx.get(LLMS_TXT_URL).mock(return_value=httpx.Response(200, content=b"# example.com\n"))

    result = run(crawl_domain(DOMAIN))

    assert result.domain == DOMAIN
    assert result.agents_json.present is True
    assert result.web_bot_auth.present is False
    assert result.llms_txt.present is True
    # No chain/contract/RPC endpoint specified anywhere -- always None.
    assert result.on_chain_ref is None


@respx.mock
def test_crawl_domain_unreachable_for_all_signals():
    respx.get(AGENTS_JSON_URL).mock(side_effect=httpx.ConnectError("no route to host"))
    respx.get(WEB_BOT_AUTH_URL).mock(side_effect=httpx.ConnectError("no route to host"))
    respx.get(LLMS_TXT_URL).mock(side_effect=httpx.ConnectError("no route to host"))

    result = run(crawl_domain(DOMAIN))

    assert result.agents_json.present is False
    assert result.agents_json.error is not None
    assert result.web_bot_auth.present is False
    assert result.web_bot_auth.error is not None
    assert result.llms_txt.present is False
    assert result.llms_txt.error is not None


@respx.mock
def test_crawl_domain_runs_its_three_fetches_concurrently_not_sequentially():
    """The specific behavior this on-demand-crawl-latency change is for:
    a real waiting user's worst-case wait must be roughly ONE fetch's
    delay, not the sum of all three -- see fetch.py's `crawl_domain`
    docstring. Each mocked endpoint sleeps for `DELAY_SECONDS`; if the
    three awaits were still sequential (the old behavior) this would
    take >= 3 * DELAY_SECONDS. Run concurrently via `asyncio.gather`,
    it should take roughly one DELAY_SECONDS, comfortably under the
    2x-single-delay threshold this test asserts.
    """
    DELAY_SECONDS = 0.3

    async def _slow_200(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(DELAY_SECONDS)
        return httpx.Response(200, content=b"ok")

    respx.get(AGENTS_JSON_URL).mock(side_effect=_slow_200)
    respx.get(WEB_BOT_AUTH_URL).mock(side_effect=_slow_200)
    respx.get(LLMS_TXT_URL).mock(side_effect=_slow_200)

    started_at = time.monotonic()
    result = run(crawl_domain(DOMAIN))
    elapsed = time.monotonic() - started_at

    assert result.agents_json.present is True
    assert result.web_bot_auth.present is True
    assert result.llms_txt.present is True
    # Sequential would be >= 3 * DELAY_SECONDS (~0.9s); concurrent stays
    # well under 2 * DELAY_SECONDS even with scheduling overhead.
    assert elapsed < DELAY_SECONDS * 2, (
        f"expected the three fetches to run concurrently (~{DELAY_SECONDS}s), took {elapsed:.3f}s -- "
        "looks sequential"
    )


@respx.mock
def test_crawl_domain_skips_llms_txt_fetch_when_disabled(monkeypatch):
    import crawler.fetch as fetch_module

    monkeypatch.setattr(fetch_module, "CRAWL_LLMS_TXT_ENABLED", False)
    respx.get(AGENTS_JSON_URL).mock(return_value=httpx.Response(200, json={"agents": []}))
    respx.get(WEB_BOT_AUTH_URL).mock(return_value=httpx.Response(404))
    # Deliberately NOT mocking LLMS_TXT_URL -- if crawl_domain fetched it
    # anyway despite the flag being off, respx would raise for the
    # unmocked request and this test would fail for that reason.

    result = run(crawl_domain(DOMAIN))

    assert result.llms_txt.present is False
    assert result.llms_txt.error is None  # never attempted, not a real network failure
