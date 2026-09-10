"""STUB: on-chain agent-identity registry lookup.

--- Why this is a stub, not an integration -----------------------------

The build plan mentions "on-chain registry entries for a domain" in
exactly one place, with zero specifics: no blockchain named, no
registry contract address, no ABI, no RPC endpoint, and no registry
protocol/standard referenced anywhere in the plan or the rest of this
repo. There is nothing to integrate with -- naming a chain (Ethereum
mainnet? an L2? a purpose-built app-chain?), a specific registry
contract, and an RPC provider would all be invented from nothing, which
would just be fiction dressed up as a real integration.

This module exists so `Domain.on_chain_ref` (already present in the
schema since Phase 0, always `None` until now) has a real, feature-flagged
call site wired up -- `CRAWL_ON_CHAIN_ENABLED` (see config.py, default
`false`) -- ready for real logic to be dropped in once product defines:

  1. Which chain (mainnet L1, a specific L2, a private/consortium chain?).
  2. The registry contract's address + ABI (or equivalent for a
     non-EVM chain) and what "an entry for this domain" even means
     there (a direct mapping? an event log to index? a subgraph to
     query?).
  3. Which RPC endpoint/provider this service is allowed to call, and
     its own reliability/rate-limit/cost characteristics (on-chain RPC
     calls are slower and less reliable than a normal HTTP fetch --
     this would need its own timeout/retry/circuit-breaking strategy,
     not just reusing fetch.py's).

Flagging this prominently in the Phase 5 report as something needing
real product input, not an engineering gap that can just be closed by
writing more code.

--- What this actually does right now -----------------------------------

Regardless of the flag, this returns `None` -- there is no backend to
query. The flag exists purely so "on-chain lookup is attempted" is an
explicit, observable, toggleable state (for when real logic lands)
rather than dead code with no way to turn it on/off. Enabling the flag
today changes nothing observable; it is deliberately still a no-op
until points 1-3 above are answered by product and a real
implementation replaces this function's body.
"""

from __future__ import annotations

from .config import CRAWL_ON_CHAIN_ENABLED


def lookup_on_chain_ref(domain: str) -> str | None:
    """Look up `domain`'s on-chain agent-identity registry entry, if
    any. Always returns `None` today -- see module docstring for why.

    Never raises: whether the flag is on or off, this must be as safe
    to call as "no signal available," never a source of crawl failures.
    """
    if not CRAWL_ON_CHAIN_ENABLED:
        return None

    # STUB: with the flag on, a real implementation would resolve
    # `domain` against a specified chain's registry contract via a
    # configured RPC endpoint here. No chain/contract/RPC endpoint is
    # specified anywhere in this codebase or the build plan (see module
    # docstring), so this intentionally still returns None rather than
    # fabricating a fake protocol to make the flag "do something."
    return None


__all__ = ["lookup_on_chain_ref"]
