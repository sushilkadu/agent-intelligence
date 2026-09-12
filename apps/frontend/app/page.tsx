"use client";

import Link from "next/link";
import { FormEvent, ReactNode, useEffect, useState } from "react";

// Configurable so this same build can point at a deployed api-service
// (Phase 3's HTTP API) instead of a local one -- defaults to
// api-service's local `uvicorn` server for local dev (see
// services/api-service/app.py).
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// GET /v1/domains/{domain}'s cache-miss path now triggers a real
// on-demand crawl (202 "pending") instead of a dead-end 404 -- see
// services/api-service/api/routes.py's `_trigger_on_demand_crawl`.
// This page polls the SAME endpoint until the record lands (or a
// bounded timeout passes) instead of asking the visitor to retry
// manually.
//
// POLL_INTERVAL_MS mirrors api-service's own `retry_after_seconds`
// default (see api/config.py's `CRAWL_POLL_INTERVAL_SECONDS`) -- kept
// as a plain constant here rather than reading the value out of the
// 202 body, since polling faster than ~2s buys nothing (a crawl+parse
// round trip never completes that quickly) and this page has no way
// to poll SLOWER without also slowing down the very first check.
// POLL_TIMEOUT_MS budgets for crawler-service's now-parallelized fetch
// (worst case roughly one fetch timeout, see
// services/crawler-service/crawler/fetch.py's `crawl_domain`
// docstring) plus the S3 write -> SQS -> parser-service upsert round
// trip, with comfortable headroom.
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 40000;

// Purely cosmetic, honest engagement while polling: real progress
// through the crawl isn't observable from here (the API only ever
// reports "still pending" vs. "found," never which specific signal
// finished first -- see the docstring on the `pending` state below),
// so this cycles on a fixed timer rather than claiming to track real
// sub-progress.
const CRAWL_STAGE_MESSAGES = [
  "Checking agents.json…",
  "Checking the Web Bot Auth directory…",
  "Checking llms.txt…",
  "Some sites take a little longer to answer…",
];
const STAGE_MESSAGE_INTERVAL_MS = 2500;

// Mirrors packages/shared-schema/shared_schema/models.py's `Domain`
// (api-service's `GET /v1/domains/{domain}` returns this shape
// as-is -- see services/api-service/api/schemas.py's docstring on
// that choice). Only the fields this page actually renders are
// typed here; the response may carry more.
interface DomainRecord {
  domain: string;
  first_seen_at: string;
  last_crawled_at: string;
  agent_json_present: boolean;
  llms_txt_present: boolean;
  web_bot_auth_present: boolean;
  web_bot_auth_valid: boolean;
  web_bot_auth_expiry: string | null;
  confidence_flags: string[];
}

interface ApiErrorBody {
  error?: string;
  message?: string;
}

type LookupState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "pending"; domain: string; pollToken: number }
  | { status: "found"; record: DomainRecord }
  | { status: "unsafe_domain"; domain: string; message: string }
  | { status: "timed_out"; domain: string }
  | { status: "rate_limited"; message: string }
  | { status: "error"; message: string };

// Translates parser-service's `confidence_flags` (see
// services/parser-service/parser/confidence.py) into short,
// human-readable sentences for this public lookup tool -- the raw
// flag strings are an internal/API vocabulary, not something a
// non-technical visitor should have to decode.
const CONFIDENCE_FLAG_MESSAGES: Record<string, string> = {
  expired_key: "This domain's Web Bot Auth signing key has expired.",
  malformed_manifest: "This domain's agents.json couldn't be parsed.",
  malformed_web_bot_auth: "This domain's Web Bot Auth directory couldn't be parsed.",
  no_signals: "No agent-identity signals were found for this domain.",
};

function describeConfidenceFlag(flag: string): string {
  return CONFIDENCE_FLAG_MESSAGES[flag] ?? `Unrecognized signal: ${flag}`;
}

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

let pollTokenCounter = 0;

export default function Home() {
  const [domainInput, setDomainInput] = useState("");
  const [state, setState] = useState<LookupState>({ status: "idle" });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const domain = domainInput.trim().toLowerCase();
    if (!domain) return;

    setState({ status: "loading" });
    const outcome = await fetchDomainOutcome(domain);
    setState(
      outcome.kind === "pending" ? { status: "pending", domain, pollToken: ++pollTokenCounter } : outcome.state,
    );
  }

  // Keyed off `pollToken` (assigned once, above, the moment a lookup
  // FIRST comes back 202), extracted to a plain variable so the
  // dependency array below is a single identifier ESLint can check
  // statically, not an inline expression.
  const pendingPollToken = state.status === "pending" ? state.pollToken : null;

  // Drives the polling loop while `state.status === "pending"`. Reruns
  // only when `pendingPollToken` changes (a NEW pending episode, i.e.
  // a fresh manual submit) -- NOT on every `state` change, since a
  // still-pending poll deliberately does NOT call `setState` at all
  // (see `poll` below), so this effect's one-time `deadline`
  // computation isn't repeatedly reset by its own polling and
  // `POLL_TIMEOUT_MS` means what it says. `state` itself is read only
  // to capture `domain` at effect-start time, not reacted to.
  useEffect(() => {
    if (state.status !== "pending") return;
    const { domain } = state;

    let cancelled = false;
    let timer: number;
    const deadline = Date.now() + POLL_TIMEOUT_MS;

    async function poll() {
      if (cancelled) return;
      if (Date.now() >= deadline) {
        setState({ status: "timed_out", domain });
        return;
      }

      const outcome = await fetchDomainOutcome(domain);
      if (cancelled) return;

      if (outcome.kind === "pending") {
        timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      } else {
        setState(outcome.state);
      }
    }

    timer = window.setTimeout(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally keyed on pendingPollToken only, see comment above
  }, [pendingPollToken]);

  return (
    <div className="flex min-h-screen flex-col items-center bg-zinc-50 px-4 py-16 font-sans dark:bg-black sm:py-24">
      <main className="flex w-full max-w-xl flex-col items-center gap-8">
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-3xl font-semibold tracking-tight text-black dark:text-zinc-50">
            Agent Intelligence
          </h1>
          <p className="max-w-sm text-sm text-zinc-500 dark:text-zinc-400">
            Check any domain&apos;s agent-readiness: whether it publishes agents.json, llms.txt, or
            a Web Bot Auth directory for AI agents to discover.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex w-full gap-2">
          <input
            type="text"
            inputMode="url"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="example.com"
            value={domainInput}
            onChange={(event) => setDomainInput(event.target.value)}
            className="w-full rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-black outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:focus:border-zinc-400"
          />
          <button
            type="submit"
            disabled={
              (state.status === "loading" || state.status === "pending") || domainInput.trim().length === 0
            }
            className="shrink-0 rounded-lg bg-black px-4 py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-40 dark:bg-zinc-50 dark:text-black"
          >
            {state.status === "loading" || state.status === "pending" ? "Checking…" : "Check domain"}
          </button>
        </form>

        <ResultsPanel state={state} />

        <Link
          href="/dashboard"
          className="text-xs text-zinc-400 underline underline-offset-2 dark:text-zinc-500"
        >
          Need bulk lookups or a higher rate limit? Subscribe or sign in →
        </Link>
      </main>
    </div>
  );
}

// Either "still pending, nothing to show yet" (the caller decides what
// that means -- an initial submit turns it into the `pending` state; a
// poll iteration that's already in the `pending` state just tries
// again without touching React state at all) or a final, settled
// `LookupState` to render.
type LookupOutcome = { kind: "pending" } | { kind: "settled"; state: LookupState };

/**
 * One GET /v1/domains/{domain} call, translated into a `LookupOutcome`.
 * Deliberately does not call `setState` itself -- both call sites
 * (the initial submit in `handleSubmit` and each iteration of the
 * polling loop in `Home`'s effect) need to react to a "still pending"
 * result differently, so the decision of what that means for React
 * state stays with them.
 */
async function fetchDomainOutcome(domain: string): Promise<LookupOutcome> {
  try {
    const response = await fetch(`${API_BASE_URL}/v1/domains/${encodeURIComponent(domain)}`);

    if (response.status === 202) {
      return { kind: "pending" };
    }
    if (response.status === 400) {
      const body: ApiErrorBody = await response.json().catch(() => ({}));
      return {
        kind: "settled",
        state: { status: "unsafe_domain", domain, message: body.message ?? `'${domain}' can't be checked.` },
      };
    }
    if (response.status === 429) {
      const body: ApiErrorBody = await response.json().catch(() => ({}));
      return {
        kind: "settled",
        state: {
          status: "rate_limited",
          message: body.message ?? "Too many requests -- please slow down and try again shortly.",
        },
      };
    }
    if (!response.ok) {
      const body: ApiErrorBody = await response.json().catch(() => ({}));
      return {
        kind: "settled",
        state: { status: "error", message: body.message ?? `Something went wrong (HTTP ${response.status}).` },
      };
    }

    const record = (await response.json()) as DomainRecord;
    return { kind: "settled", state: { status: "found", record } };
  } catch {
    return {
      kind: "settled",
      state: { status: "error", message: "Couldn't reach the Agent Intelligence API. Is it running?" },
    };
  }
}

function ResultsPanel({ state }: { state: LookupState }) {
  if (state.status === "idle") {
    return null;
  }

  return (
    <div className="w-full rounded-lg border border-zinc-200 bg-white p-5 text-sm dark:border-zinc-800 dark:bg-zinc-950">
      {state.status === "loading" && (
        <p className="text-zinc-500 dark:text-zinc-400">Looking this domain up…</p>
      )}

      {/* `key` forces a fresh mount (and a reset `stageIndex`) per
          pending episode, instead of an effect reaching back in to
          reset state on an existing instance. */}
      {state.status === "pending" && <PendingCrawl key={state.pollToken} domain={state.domain} />}

      {state.status === "timed_out" && (
        <div className="flex flex-col gap-1">
          <p className="font-medium text-black dark:text-zinc-50">Taking longer than expected.</p>
          <p className="text-zinc-500 dark:text-zinc-400">
            We&apos;re still crawling &ldquo;{state.domain}&rdquo; -- check back in a moment and
            search it again.
          </p>
        </div>
      )}

      {state.status === "unsafe_domain" && (
        <div className="flex flex-col gap-1">
          <p className="font-medium text-black dark:text-zinc-50">Can&apos;t check that domain.</p>
          <p className="text-zinc-500 dark:text-zinc-400">{state.message}</p>
        </div>
      )}

      {state.status === "rate_limited" && (
        <div className="flex flex-col gap-1">
          <p className="font-medium text-black dark:text-zinc-50">Slow down a little.</p>
          <p className="text-zinc-500 dark:text-zinc-400">{state.message}</p>
        </div>
      )}

      {state.status === "error" && (
        <div className="flex flex-col gap-1">
          <p className="font-medium text-black dark:text-zinc-50">Something went wrong.</p>
          <p className="text-zinc-500 dark:text-zinc-400">{state.message}</p>
        </div>
      )}

      {state.status === "found" && <FoundResult record={state.record} />}
    </div>
  );
}

/**
 * The "crawl in progress" experience: an indeterminate spinner plus a
 * cycling line of copy naming what's actually being checked. The
 * cycling is purely a visual-engagement device on a fixed timer, NOT a
 * claim about real sub-progress -- api-service only ever reports
 * "still pending" vs. "found" (see api/routes.py's
 * `_trigger_on_demand_crawl`), never which individual signal has
 * completed, so this never asserts one has.
 */
function PendingCrawl({ domain }: { domain: string }) {
  const [stageIndex, setStageIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setStageIndex((i) => (i + 1) % CRAWL_STAGE_MESSAGES.length);
    }, STAGE_MESSAGE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-zinc-300 border-t-black dark:border-zinc-700 dark:border-t-zinc-50"
        />
        <p className="font-medium text-black dark:text-zinc-50">
          Crawling &ldquo;{domain}&rdquo; for the first time…
        </p>
      </div>
      <p className="text-zinc-500 dark:text-zinc-400">{CRAWL_STAGE_MESSAGES[stageIndex]}</p>
      <p className="text-xs text-zinc-400 dark:text-zinc-500">
        Nobody has searched this domain before, so we&apos;re fetching it live -- this usually takes
        a few seconds.
      </p>
    </div>
  );
}

function FoundResult({ record }: { record: DomainRecord }) {
  const webBotAuthLine = !record.web_bot_auth_present
    ? "No Web Bot Auth directory found."
    : record.web_bot_auth_valid
      ? "Web Bot Auth found, with a currently valid signing key."
      : "Web Bot Auth found, but its signing key is not currently valid.";

  return (
    <div className="flex flex-col gap-4">
      <p className="font-medium text-black dark:text-zinc-50">
        Results for <span className="font-mono">{record.domain}</span>
      </p>

      <ul className="flex flex-col gap-2">
        <ResultRow ok={record.agent_json_present}>
          {record.agent_json_present ? "agents.json found." : "No agents.json found."}
        </ResultRow>
        <ResultRow ok={record.llms_txt_present}>
          {record.llms_txt_present ? "llms.txt found." : "No llms.txt found."}
        </ResultRow>
        <ResultRow ok={record.web_bot_auth_present && record.web_bot_auth_valid}>
          {webBotAuthLine}
        </ResultRow>
      </ul>

      {record.confidence_flags.length > 0 && (
        <div className="flex flex-col gap-1 rounded-md bg-amber-50 p-3 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          {record.confidence_flags.map((flag) => (
            <p key={flag}>{describeConfidenceFlag(flag)}</p>
          ))}
        </div>
      )}

      <p className="text-xs text-zinc-400 dark:text-zinc-500">
        Last crawled {formatTimestamp(record.last_crawled_at)} -- first seen{" "}
        {formatTimestamp(record.first_seen_at)}.
      </p>
    </div>
  );
}

function ResultRow({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      <span
        aria-hidden
        className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white ${
          ok ? "bg-green-600" : "bg-zinc-400 dark:bg-zinc-600"
        }`}
      >
        {ok ? "✓" : "–"}
      </span>
      <span className="text-zinc-700 dark:text-zinc-300">{children}</span>
    </li>
  );
}
