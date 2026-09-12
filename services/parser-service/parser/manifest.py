"""Validation/parsing of `agents.json`.

IMPORTANT -- read before touching this file: there is no single
ratified spec for `agents.json` as of this writing. Multiple
competing/incompatible proposals exist in the wild (wildcard-ai's
`agents.json` built on OpenAPI/Arazzo, `agent.json`/the "Agent Web
Protocol", `ai-agent.json`, JSON Agents/PAM, and others), and the
original Agent Intelligence build plan's own description of the
artifact is deliberately generic: "a JSON manifest... describing its
API endpoints, auth methods, and how agents can interact with it."

Given that, this module makes a deliberate choice: it does NOT
hard-validate the parsed document against any one of those external
schemas (doing so would silently reject manifests that are perfectly
valid under a different, equally legitimate proposal -- or valid under
none of them, which today is most of the crawlable web). Instead it
matches the precedent crawler-service already set at fetch time (store
raw bytes, no interpretation) and applies exactly two structural
checks, both things *every* one of those proposals agrees on:

  1. The bytes decode as UTF-8 and parse as JSON at all.
  2. The parsed JSON value's top level is an object (a dict), not a
     bare list/string/number -- every competing proposal describes the
     manifest as a JSON *object* with named top-level keys.

If both hold, the parsed object is stored as-is into
`declared_capabilities` -- callers (api-service, a future UI) can
introspect whatever fields a given site chose to publish rather than
this service silently discarding fields it doesn't recognize under one
particular schema. If either check fails, that's the
`malformed_manifest` confidence flag (see confidence.py) and
`declared_capabilities` is left empty; either way this never raises,
since a malformed manifest on one domain must not take down the batch.

Flag whoever reviews this before Phase 3: if/when one of these
proposals (or an IETF draft) becomes a clear de facto standard, this is
the place to add real structural validation against it -- e.g.
requiring specific keys, validating endpoint URLs, etc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedManifest:
    """Result of validating one `agents.json` payload.

    `malformed_reason` is a short, specific, human-readable diagnostic
    (e.g. "top-level value is a JSON array, not a JSON object") --
    always set together with `malformed=True`, always `None` otherwise.
    Added because a bare "couldn't be parsed" told a site owner
    debugging their own manifest THAT something was wrong but never
    WHAT -- exactly the gap a real user flagged after this page started
    showing that generic message. `str | None` (not baked into a fixed
    enum of reasons) because the specific wording is meant for a human
    reading the lookup page, not for a machine branching on it -- a
    consumer that needs to distinguish failure modes programmatically
    already has `malformed: bool`.
    """

    declared_capabilities: dict[str, Any] = field(default_factory=dict)
    malformed: bool = False
    malformed_reason: str | None = None


def _json_type_name(value: Any) -> str:
    """A short, human-readable name for a JSON value's type, used only
    to make the "not an object" `malformed_reason` specific (e.g. "a
    JSON array" rather than a generic "not an object").
    """
    if isinstance(value, list):
        return "a JSON array"
    if isinstance(value, str):
        return "a JSON string"
    if isinstance(value, bool):
        return "a JSON boolean"
    if isinstance(value, (int, float)):
        return "a JSON number"
    if value is None:
        return "JSON null"
    return "not a JSON object"  # pragma: no cover -- json.loads can't produce anything else


def parse_agents_json(raw_bytes: bytes | None) -> ParsedManifest:
    """Parse `raw_bytes` (the decoded body of a fetched `agents.json`)
    per the lenient rules documented above. `raw_bytes=None` (the
    signal was never fetched) is not malformed -- that's `no_signals`
    territory, a separate concern handled in confidence.py.
    """
    if raw_bytes is None:
        return ParsedManifest()

    try:
        text = raw_bytes.decode("utf-8")
        parsed = json.loads(text)
    except UnicodeDecodeError:
        return ParsedManifest(malformed=True, malformed_reason="the response body isn't valid UTF-8 text")
    except json.JSONDecodeError as exc:
        return ParsedManifest(malformed=True, malformed_reason=f"not valid JSON ({exc.msg})")

    if not isinstance(parsed, dict):
        return ParsedManifest(
            malformed=True,
            malformed_reason=f"the top-level value is {_json_type_name(parsed)}, not a JSON object",
        )

    return ParsedManifest(declared_capabilities=parsed, malformed=False)


__all__ = ["ParsedManifest", "parse_agents_json"]
