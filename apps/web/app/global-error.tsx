"use client";

// No i18n here — this renders when the root layout crashes,
// so there is no LinguiClientProvider in the tree.

import "./globals.css";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main className="flex min-h-[60vh] flex-col items-center justify-center p-8 text-center">
          <h1 className="mb-2 text-2xl font-bold text-foreground">Something went wrong</h1>
          <p className="mb-6 text-sm text-muted">
            {error.digest ? `Error ID: ${error.digest}` : error.message}
          </p>
          <button className="cursor-pointer rounded-full border-none bg-primary px-6 py-2.5 text-sm font-semibold text-primary-contrast hover:opacity-85" onClick={reset}>
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
