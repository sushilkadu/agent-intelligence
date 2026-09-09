"""Unit tests for crawler.fetch -- all HTTP mocked via respx, no real network."""

from __future__ import annotations

import asyncio

import httpx
import respx

from crawler.config import AGENTS_JSON_PATH, WEB_BOT_AUTH_WELL_KNOWN_PATH
from crawler.fetch import (
    build_url,
    crawl_domain,
    fetch_agents_json,
    fetch_web_bot_auth_directory,
)

DOMAIN = "example.com"
AGENTS_JSON_URL = build_url(DOMAIN, AGENTS_JSON_PATH)
WEB_BOT_AUTH_URL = build_url(DOMAIN, WEB_BOT_AUTH_WELL_KNOWN_PATH)


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


# --- combined crawl_domain -------------------------------------------------


@respx.mock
def test_crawl_domain_fetches_both_signals():
    respx.get(AGENTS_JSON_URL).mock(return_value=httpx.Response(200, json={"agents": []}))
    respx.get(WEB_BOT_AUTH_URL).mock(return_value=httpx.Response(404))

    result = run(crawl_domain(DOMAIN))

    assert result.domain == DOMAIN
    assert result.agents_json.present is True
    assert result.web_bot_auth.present is False


@respx.mock
def test_crawl_domain_unreachable_for_both_signals():
    respx.get(AGENTS_JSON_URL).mock(side_effect=httpx.ConnectError("no route to host"))
    respx.get(WEB_BOT_AUTH_URL).mock(side_effect=httpx.ConnectError("no route to host"))

    result = run(crawl_domain(DOMAIN))

    assert result.agents_json.present is False
    assert result.agents_json.error is not None
    assert result.web_bot_auth.present is False
    assert result.web_bot_auth.error is not None
