"""Configuration constants for crawler-service.

Centralizes anything that's likely to change later (the Web Bot Auth
well-known path is still in an active IETF draft, timeouts, env var
names) so there's exactly one place to update it.
"""

from __future__ import annotations

import os

# --- Well-known signal paths -------------------------------------------------

# Web Bot Auth "signature-agent-card" discovery location. Per the
# current IETF drafts (draft-meunier-web-bot-auth-architecture,
# draft-meunier-http-message-signatures-directory,
# draft-meunier-webbotauth-registry) this is served as a JWKS document
# containing the bot/agent's public signing keys:
#   https://datatracker.ietf.org/doc/html/draft-meunier-web-bot-auth-architecture
#   https://datatracker.ietf.org/doc/html/draft-meunier-http-message-signatures-directory
#   https://datatracker.ietf.org/doc/draft-meunier-webbotauth-registry/
#
# The draft is still moving -- this is deliberately kept as the single
# named constant used everywhere the path is needed. Do not hardcode
# the literal string anywhere else; update it here if the draft's
# well-known path changes.
WEB_BOT_AUTH_WELL_KNOWN_PATH = "/.well-known/http-message-signatures-directory"

AGENTS_JSON_PATH = "/agents.json"

# llms.txt -- an emerging community convention (no single formal spec,
# unlike the Web Bot Auth IETF drafts above) for a plain-text file
# describing a site for LLM consumption. Fetched the exact same way as
# agents.json (see fetch.py's `fetch_llms_txt`) -- Phase 5 adds this as
# a third well-known signal, independently feature-flagged (see
# CRAWL_LLMS_TXT_ENABLED below) since it's a separate, still-informal
# convention from the other two.
LLMS_TXT_PATH = "/llms.txt"

# --- Feature flags (Phase 5) --------------------------------------------------
#
# Both default differently on purpose:
#   * llms.txt fetching is a straightforward, low-risk mirror of the
#     existing agents.json fetch (same code path, same failure
#     handling) -- on by default.
#   * The on-chain registry check (see onchain.py) is a documented
#     STUB: the build plan never named a chain, contract, or registry
#     protocol to integrate with, so there is nothing real for this
#     flag to turn on yet. Defaults to false so enabling it (once a
#     real spec exists) is an explicit, deliberate opt-in rather than
#     something that silently starts running.
CRAWL_LLMS_TXT_ENABLED = os.environ.get("CRAWL_LLMS_TXT_ENABLED", "true").strip().lower() in ("1", "true", "yes")
CRAWL_ON_CHAIN_ENABLED = os.environ.get("CRAWL_ON_CHAIN_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# --- HTTP fetch behavior ------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("CRAWLER_TIMEOUT_SECONDS", "10"))

# Overrides the URL scheme used to build fetch URLs. Defaults to
# "https" (real crawling). Local dev / test tooling can set this to
# "http" to point the crawl logic at a local mock HTTP server instead
# of doing a real TLS handshake -- see scripts/verify_localstack_e2e.py.
URL_SCHEME = os.environ.get("CRAWLER_URL_SCHEME", "https")

# --- AWS resource configuration ------------------------------------------------
#
# `AWS_ENDPOINT_URL` is the standard boto3/botocore env var: when set
# (locally, to LocalStack's endpoint), every boto3 client built by this
# service targets it; when unset (real Lambda / real AWS), boto3
# resolves the real regional endpoints. This module only *reads* that
# env var -- the literal LocalStack URL (http://localhost:4566) lives
# in local dev config/scripts (docker-compose, .env files, the
# verification script), never here.

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

RAW_DATA_BUCKET_NAME = os.environ.get("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-dev")
CRAWL_QUEUE_URL = os.environ.get("CRAWL_QUEUE_URL", "")
RAW_FETCHED_QUEUE_URL = os.environ.get("RAW_FETCHED_QUEUE_URL", "")


def boto3_client_kwargs() -> dict:
    """Common kwargs for every boto3 client this service creates.

    Centralized so every call site (storage.py, messaging.py, the seed
    loader script) picks up `AWS_ENDPOINT_URL` / `AWS_REGION` the same
    way instead of duplicating the same three lines.
    """
    kwargs: dict = {"region_name": AWS_REGION}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs
