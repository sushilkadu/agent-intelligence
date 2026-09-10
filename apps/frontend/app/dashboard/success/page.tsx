import { Suspense } from "react";

import SuccessContent from "./success-content";

// `SuccessContent` reads `?session_id=...` via `useSearchParams`, which
// Next.js requires to be wrapped in a Suspense boundary for a
// production build to succeed (see Next's own docs on
// "Missing Suspense boundary with useSearchParams") -- otherwise
// `npm run build` fails on this route.
export default function DashboardSuccessPage() {
  return (
    <Suspense fallback={<LoadingFallback />}>
      <SuccessContent />
    </Suspense>
  );
}

function LoadingFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 px-4 dark:bg-black">
      <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
    </div>
  );
}
