"use client";

import { FormEvent, useEffect, useState } from "react";

// Two separate backends, per the Phase 4 architecture: api-service
// (domain lookups, key usage) and billing-service (Stripe
// checkout/webhook/portal) are independently deployable services with
// their own API Gateway instances -- see infra/terraform/envs/dev/main.tf.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const BILLING_BASE_URL = process.env.NEXT_PUBLIC_BILLING_BASE_URL ?? "http://localhost:8001";

// This architecture has no login/session system, and building one is
// out of scope for Phase 4 (see the build plan). The deliberate,
// lightweight interpretation of "authenticated dashboard" here: paste
// your API key, it's remembered in this browser's localStorage for
// convenience on return visits -- the same "sign in with a token"
// pattern plenty of real dev-tool SaaS products use (a CLI/API key
// pasted into a settings page). This is NOT a session/cookie/auth
// system: the key never leaves this browser except as the X-API-Key
// header on requests the user's own browser makes directly to
// api-service/billing-service.
const STORED_API_KEY_STORAGE_KEY = "agent-intel-api-key";

interface KeyUsage {
  key_id: string;
  plan_tier: string;
  rate_limit: number;
  window_seconds: number;
  current_window_count: number;
}

interface ApiErrorBody {
  error?: string;
  message?: string;
}

type DashboardState =
  | { status: "checking" }
  | { status: "signed_out" }
  | { status: "invalid_key" }
  | { status: "signed_in"; apiKey: string; usage: KeyUsage };

function readStoredApiKey(): string | null {
  try {
    return window.localStorage.getItem(STORED_API_KEY_STORAGE_KEY);
  } catch {
    // Private-browsing / storage-blocked browsers can throw on access,
    // not just on write -- treat that the same as "nothing stored."
    return null;
  }
}

export default function DashboardPage() {
  // Always starts at "checking" -- the SAME value on the server-rendered
  // HTML and the client's first render -- rather than deciding
  // signed_in/signed_out from localStorage in a lazy initializer.
  // localStorage doesn't exist during server rendering, so an
  // initializer that reads it would compute a DIFFERENT value on the
  // server vs. the client's initial render, which is exactly what
  // causes a React hydration-mismatch error (confirmed by hitting this
  // for real while testing this page in a browser -- see the Phase 4
  // report). The effect below is what's actually allowed to read
  // localStorage, since effects only run client-side, after hydration.
  const [state, setState] = useState<DashboardState>({ status: "checking" });
  const [keyInput, setKeyInput] = useState("");
  const [email, setEmail] = useState("");
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);
  const [portalError, setPortalError] = useState<string | null>(null);

  useEffect(() => {
    // Routed through one async function (rather than calling setState
    // directly in the effect body for the "signed_out" branch) so this
    // is a genuine post-mount synchronization step, not a synchronous
    // render-time decision -- see loadUsage below, which this shares
    // its "checking" -> real-status transition with.
    async function determineSignInState() {
      const stored = readStoredApiKey();
      if (!stored) {
        setState({ status: "signed_out" });
        return;
      }
      await loadUsage(stored);
    }
    void determineSignInState();
  }, []);

  async function loadUsage(apiKey: string) {
    setState({ status: "checking" });
    try {
      const response = await fetch(`${API_BASE_URL}/v1/keys/me`, {
        headers: { "X-API-Key": apiKey },
      });
      if (!response.ok) {
        setState({ status: "invalid_key" });
        return;
      }
      const usage = (await response.json()) as KeyUsage;
      setState({ status: "signed_in", apiKey, usage });
    } catch {
      setState({ status: "invalid_key" });
    }
  }

  function handleSignIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = keyInput.trim();
    if (!trimmed) return;
    try {
      window.localStorage.setItem(STORED_API_KEY_STORAGE_KEY, trimmed);
    } catch {
      // Storage unavailable -- sign-in still works for this page load,
      // it just won't be remembered on the next visit.
    }
    void loadUsage(trimmed);
  }

  function handleSignOut() {
    try {
      window.localStorage.removeItem(STORED_API_KEY_STORAGE_KEY);
    } catch {
      // ignore -- nothing to clean up if storage was never usable.
    }
    setKeyInput("");
    setState({ status: "signed_out" });
  }

  async function handleCheckout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;

    setCheckoutLoading(true);
    setCheckoutError(null);
    try {
      const response = await fetch(`${BILLING_BASE_URL}/v1/billing/checkout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: trimmed, plan_tier: "self_serve" }),
      });
      if (!response.ok) {
        const body: ApiErrorBody = await response.json().catch(() => ({}));
        setCheckoutError(body.message ?? `Something went wrong (HTTP ${response.status}).`);
        return;
      }
      const body = (await response.json()) as { checkout_url: string };
      window.location.href = body.checkout_url;
    } catch {
      setCheckoutError("Couldn't reach the billing service. Is it running?");
      setCheckoutLoading(false);
    }
  }

  async function handleManageSubscription(apiKey: string) {
    setPortalLoading(true);
    setPortalError(null);
    try {
      const response = await fetch(`${BILLING_BASE_URL}/v1/billing/portal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey }),
      });
      if (!response.ok) {
        const body: ApiErrorBody = await response.json().catch(() => ({}));
        setPortalError(body.message ?? `Something went wrong (HTTP ${response.status}).`);
        return;
      }
      const body = (await response.json()) as { portal_url: string };
      window.location.href = body.portal_url;
    } catch {
      setPortalError("Couldn't reach the billing service. Is it running?");
      setPortalLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center bg-zinc-50 px-4 py-16 font-sans dark:bg-black sm:py-24">
      <main className="flex w-full max-w-xl flex-col items-center gap-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-3xl font-semibold tracking-tight text-black dark:text-zinc-50">Dashboard</h1>
          <p className="max-w-sm text-sm text-zinc-500 dark:text-zinc-400">
            Subscribe for bulk domain lookups and a higher rate limit, or sign in with an existing API key.
          </p>
        </div>

        {state.status === "checking" && <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>}

        {(state.status === "signed_out" || state.status === "invalid_key") && (
          <>
            {state.status === "invalid_key" && (
              <div className="w-full rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                That API key wasn&apos;t accepted (invalid or deactivated). Try again below.
              </div>
            )}

            <section className="w-full rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
              <h2 className="mb-3 text-sm font-semibold text-black dark:text-zinc-50">Sign in with your API key</h2>
              <form onSubmit={handleSignIn} className="flex gap-2">
                <input
                  type="password"
                  autoComplete="off"
                  placeholder="ai_live_..."
                  value={keyInput}
                  onChange={(event) => setKeyInput(event.target.value)}
                  className="w-full rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-black outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:focus:border-zinc-400"
                />
                <button
                  type="submit"
                  disabled={keyInput.trim().length === 0}
                  className="shrink-0 rounded-lg bg-black px-4 py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-40 dark:bg-zinc-50 dark:text-black"
                >
                  Sign in
                </button>
              </form>
              <p className="mt-2 text-xs text-zinc-400 dark:text-zinc-500">
                A lightweight, no-account &ldquo;sign in&rdquo;: your key is stored only in this browser so
                it&apos;s remembered on return visits. It&apos;s never sent anywhere except directly to the
                Agent Intelligence API in the X-API-Key header.
              </p>
            </section>

            <section className="w-full rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
              <h2 className="mb-3 text-sm font-semibold text-black dark:text-zinc-50">Subscribe (self-serve plan)</h2>
              <form onSubmit={handleCheckout} className="flex gap-2">
                <input
                  type="email"
                  required
                  placeholder="you@example.com"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  className="w-full rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-black outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:focus:border-zinc-400"
                />
                <button
                  type="submit"
                  disabled={checkoutLoading || email.trim().length === 0}
                  className="shrink-0 rounded-lg bg-black px-4 py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-40 dark:bg-zinc-50 dark:text-black"
                >
                  {checkoutLoading ? "Redirecting…" : "Subscribe"}
                </button>
              </form>
              {checkoutError && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{checkoutError}</p>}
              <p className="mt-2 text-xs text-zinc-400 dark:text-zinc-500">
                Need a licensing plan instead? Those are provisioned manually -- contact sales.
              </p>
            </section>
          </>
        )}

        {state.status === "signed_in" && (
          <section className="w-full rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-black dark:text-zinc-50">Your plan</h2>
              <button
                onClick={handleSignOut}
                className="text-xs text-zinc-400 underline underline-offset-2 dark:text-zinc-500"
              >
                Sign out
              </button>
            </div>
            <dl className="flex flex-col gap-2 text-sm">
              <UsageRow label="Plan tier" value={state.usage.plan_tier} />
              <UsageRow label="Rate limit" value={`${state.usage.rate_limit} req / ${state.usage.window_seconds}s`} />
              <UsageRow
                label="Current window usage"
                value={`${state.usage.current_window_count} / ${state.usage.rate_limit}`}
              />
              <UsageRow label="Key id" value={state.usage.key_id} mono />
            </dl>
            <button
              onClick={() => handleManageSubscription(state.apiKey)}
              disabled={portalLoading}
              className="mt-4 w-full rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm font-medium text-black transition-opacity disabled:opacity-40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
            >
              {portalLoading ? "Redirecting…" : "Manage subscription"}
            </button>
            {portalError && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{portalError}</p>}
          </section>
        )}
      </main>
    </div>
  );
}

function UsageRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-zinc-100 pb-2 last:border-0 dark:border-zinc-900">
      <dt className="text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className={`text-black dark:text-zinc-50 ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}
