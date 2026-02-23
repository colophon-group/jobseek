"use client";

import { Trans } from "@lingui/react/macro";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="flex min-h-[60vh] flex-col items-center justify-center p-8 text-center">
      <h1 className="mb-2 text-2xl font-bold text-foreground">
        <Trans id="error.title" comment="Heading shown when a page crashes unexpectedly">
          Something went wrong
        </Trans>
      </h1>
      <p className="mb-6 text-sm text-muted">
        {error.digest ? `Error ID: ${error.digest}` : error.message}
      </p>
      <button className="cursor-pointer rounded-full border-none bg-primary px-6 py-2.5 text-sm font-semibold text-primary-contrast hover:opacity-85" onClick={reset}>
        <Trans id="error.retryButton" comment="Button to retry loading the page after an error">
          Try again
        </Trans>
      </button>
    </main>
  );
}
