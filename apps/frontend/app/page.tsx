"use client";

import Link from "next/link";
import { FormEvent, ReactNode, useState } from "react";

// Configurable so this same build can point at a deployed api-service
// (Phase 3's HTTP API) instead of a local one -- defaults to
// api-service's local `uvicorn` server for local dev (see
// services/api-service/app.py).
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
  | { status: "found"; record: DomainRecord }
  | { status: "not_found"; domain: string }
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

export default function Home() {
  const [domainInput, setDomainInput] = useState("");
  const [state, setState] = useState<LookupState>({ status: "idle" });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const domain = domainInput.trim().toLowerCase();
    if (!domain) return;

    setState({ status: "loading" });
    try {
      const response = await fetch(`${API_BASE_URL}/v1/domains/${encodeURIComponent(domain)}`);

      if (response.status === 404) {
        setState({ status: "not_found", domain });
        return;
      }
      if (response.status === 429) {
        const body: ApiErrorBody = await response.json().catch(() => ({}));
        setState({
          status: "rate_limited",
          message: body.message ?? "Too many requests -- please slow down and try again shortly.",
        });
        return;
      }
      if (!response.ok) {
        const body: ApiErrorBody = await response.json().catch(() => ({}));
        setState({
          status: "error",
          message: body.message ?? `Something went wrong (HTTP ${response.status}).`,
        });
        return;
      }

      const record = (await response.json()) as DomainRecord;
      setState({ status: "found", record });
    } catch {
      setState({
        status: "error",
        message: "Couldn't reach the Agent Intelligence API. Is it running?",
      });
    }
  }

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
            disabled={state.status === "loading" || domainInput.trim().length === 0}
            className="shrink-0 rounded-lg bg-black px-4 py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-40 dark:bg-zinc-50 dark:text-black"
          >
            {state.status === "loading" ? "Checking…" : "Check domain"}
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

function ResultsPanel({ state }: { state: LookupState }) {
  if (state.status === "idle") {
    return null;
  }

  return (
    <div className="w-full rounded-lg border border-zinc-200 bg-white p-5 text-sm dark:border-zinc-800 dark:bg-zinc-950">
      {state.status === "loading" && (
        <p className="text-zinc-500 dark:text-zinc-400">Looking this domain up…</p>
      )}

      {state.status === "not_found" && (
        <div className="flex flex-col gap-1">
          <p className="font-medium text-black dark:text-zinc-50">
            We haven&apos;t crawled &ldquo;{state.domain}&rdquo; yet.
          </p>
          <p className="text-zinc-500 dark:text-zinc-400">
            This domain isn&apos;t in our index yet -- it hasn&apos;t been crawled, which just means
            we don&apos;t have data for it right now, not that anything is wrong with it.
          </p>
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
