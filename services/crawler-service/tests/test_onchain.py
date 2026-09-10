"""Unit tests for crawler.onchain -- the documented STUB for on-chain
agent-identity registry lookups (see onchain.py's module docstring for
why there is no real chain/contract/RPC integration here).

These tests only need to prove two things: it never crashes (with or
without the feature flag on), and it never fabricates a real-looking
value in the absence of a real backend -- always None.
"""

from __future__ import annotations

import crawler.onchain as onchain_module
from crawler.onchain import lookup_on_chain_ref


def test_lookup_on_chain_ref_is_none_when_disabled(monkeypatch):
    monkeypatch.setattr(onchain_module, "CRAWL_ON_CHAIN_ENABLED", False)

    assert lookup_on_chain_ref("example.com") is None


def test_lookup_on_chain_ref_is_none_when_enabled_with_no_real_backend(monkeypatch):
    """Even with the flag flipped on, there is no chain/contract/RPC
    endpoint configured anywhere (see module docstring) -- this must
    not crash and must not invent a value.
    """
    monkeypatch.setattr(onchain_module, "CRAWL_ON_CHAIN_ENABLED", True)

    assert lookup_on_chain_ref("example.com") is None
    assert lookup_on_chain_ref("another-example.org") is None
