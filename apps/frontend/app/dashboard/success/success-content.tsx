"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

const BILLING_BASE_URL = process.env.NEXT_PUBLIC_BILLING_BASE_URL ?? "http://localhost:8001";

const STORED_API_KEY_STORAGE_KEY = "agent-intel-api-key";

// Mirrors billing-service's `SessionKeyResponse` (see
// services/billing-service/billing/schemas.py).
interface SessionKeyResponse {
  key_id: string;
  plan_tier: string;
  rate_limit: number;
  api_key: string | null;
  already_retrieved: boolean;
}

interface ApiErrorBody {
  error?: string;
  message?: string;
}

type State =
  | { status: "loading" }
  | { status: "missing_session_id" }
  | { status: "error"; message: string }
  | { status: "ready"; data: SessionKeyResponse };

export default function SuccessContent() {
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session_id");
  // sessionId is already known synchronously from the URL at render
  // time, so the "missing session id" case is decided by the lazy
  // initializer, not a setState call inside the effect below (which
  // only needs to run for the genuinely async fetch case).
  const [state, setState] = useState<State>(() => (sessionId ? { status: "loading" } : { status: "missing_session_id" }));
  const [saved, setSaved] = useState(false);

  // GET /v1/billing/session/{id} is NOT safe to call twice for the
  // same session -- it deliberately clears the one-time secret after
  // the first read (see billing/routes.py's docstring). Found for real
  // while browser-testing this page: React 18/19's Strict Mode
  // deliberately double-invokes effects (mount -> cleanup -> mount
  // again) in development to catch exactly this class of bug. This ref
  // makes the fetch actually fire once per component instance
  // regardless of how many times the effect body itself runs, which
  // also protects against any other accidental duplicate invocation in
  // production (a fast remount, a retried request), not just Strict
  // Mode.
  //
  // Deliberately NOT paired with a `cancelled`-flag cleanup function
  // (the usual "ignore this fetch's result if the effect re-ran"
  // pattern) -- combining both was tried and tested for real: Strict
  // Mode's phantom unmount still runs the cleanup function, which set
  // `cancelled = true` on the ONE real request this ref-guard allowed,
  // so its result was silently discarded and the page hung on
  // "loading" forever. Since this effect can only ever start a single
  // real request per mount (the ref-guard's whole job), there is
  // nothing left for a second cleanup-triggered cancellation to guard
  // against.
  const hasFetchedRef = useRef(false);

  useEffect(() => {
    if (!sessionId || hasFetchedRef.current) {
      return;
    }
    hasFetchedRef.current = true;

    (async () => {
      try {
        const response = await fetch(`${BILLING_BASE_URL}/v1/billing/session/${encodeURIComponent(sessionId)}`);

        if (response.status === 404) {
          setState({
            status: "error",
            message:
              "We couldn't find this checkout session yet. If you just subscribed, the webhook may not have " +
              "landed yet -- wait a few seconds and refresh this page.",
          });
          return;
        }
        if (!response.ok) {
          const body: ApiErrorBody = await response.json().catch(() => ({}));
          setState({ status: "error", message: body.message ?? `Something went wrong (HTTP ${response.status}).` });
          return;
        }
        const data = (await response.json()) as SessionKeyResponse;
        setState({ status: "ready", data });
      } catch {
        setState({ status: "error", message: "Couldn't reach the billing service. Is it running?" });
      }
    })();
  }, [sessionId]);

  function handleSaveToBrowser(apiKey: string) {
    try {
      window.localStorage.setItem(STORED_API_KEY_STORAGE_KEY, apiKey);
      setSaved(true);
    } catch {
      setSaved(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center bg-zinc-50 px-4 py-16 font-sans dark:bg-black sm:py-24">
      <main className="flex w-full max-w-xl flex-col items-center gap-6">
        <h1 className="text-3xl font-semibold tracking-tight text-black dark:text-zinc-50">Subscription complete</h1>

        {state.status === "loading" && (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Retrieving your API key…</p>
        )}

        {state.status === "missing_session_id" && (
          <p className="text-center text-sm text-zinc-500 dark:text-zinc-400">
            No checkout session was found in the URL. Return to the{" "}
            <Link href="/dashboard" className="underline underline-offset-2">
              dashboard
            </Link>{" "}
            to sign in or subscribe.
          </p>
        )}

        {state.status === "error" && (
          <div className="w-full rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
            {state.message}
          </div>
        )}

        {state.status === "ready" && state.data.api_key !== null && (
          <IssuedKeyCard
            apiKey={state.data.api_key}
            planTier={state.data.plan_tier}
            rateLimit={state.data.rate_limit}
            saved={saved}
            onSave={handleSaveToBrowser}
          />
        )}

        {state.status === "ready" && state.data.api_key === null && (
          <div className="w-full rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-950">
            <p className="text-black dark:text-zinc-50">
              This key was already retrieved once and can no longer be shown here.
            </p>
            <p className="mt-1 text-zinc-500 dark:text-zinc-400">
              If you already saved it, sign in on the{" "}
              <Link href="/dashboard" className="underline underline-offset-2">
                dashboard
              </Link>
              . If you lost it, sign in there with a key you still have and use &ldquo;Manage subscription,&rdquo; or
              contact support.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

function IssuedKeyCard({
  apiKey,
  planTier,
  rateLimit,
  saved,
  onSave,
}: {
  apiKey: string;
  planTier: string;
  rateLimit: number;
  saved: boolean;
  onSave: (apiKey: string) => void;
}) {
  return (
    <div className="flex w-full flex-col gap-4">
      <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
        <p className="font-semibold">Save this key now -- it will not be shown again.</p>
        <p className="mt-1">
          This is a one-time display. Once you leave or refresh this page, we can&apos;t show you this exact key
          again -- if you lose it, you&apos;ll need to sign in with it once (before it&apos;s lost) or contact
          support.
        </p>
      </div>

      <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
        <p className="mb-2 text-xs text-zinc-500 dark:text-zinc-400">Your API key</p>
        <code className="block overflow-x-auto rounded-md bg-zinc-100 p-3 text-sm break-all text-black dark:bg-zinc-900 dark:text-zinc-50">
          {apiKey}
        </code>
        <p className="mt-2 text-xs text-zinc-400 dark:text-zinc-500">
          Plan: {planTier} -- Rate limit: {rateLimit} req/min
        </p>
      </div>

      <button
        onClick={() => onSave(apiKey)}
        disabled={saved}
        className="w-full rounded-lg bg-black px-4 py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-40 dark:bg-zinc-50 dark:text-black"
      >
        {saved ? "Saved to this browser" : "Save this key to this browser"}
      </button>

      <Link href="/dashboard" className="text-center text-sm text-zinc-500 underline underline-offset-2 dark:text-zinc-400">
        Go to dashboard
      </Link>
    </div>
  );
}
