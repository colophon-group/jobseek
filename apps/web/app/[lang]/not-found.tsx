"use client";

import { Trans } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";

export default function NotFound() {
  const localePath = useLocalePath();

  return (
    <main className="flex min-h-[60vh] flex-col items-center justify-center p-8 text-center">
      <h1 className="mb-2 text-2xl font-bold text-foreground">
        <Trans id="notFound.title" comment="Heading shown on the 404 page">
          Page not found
        </Trans>
      </h1>
      <p className="mb-6 text-sm text-muted">
        <Trans id="notFound.body" comment="Body text on the 404 page">
          The page you are looking for does not exist or has been moved.
        </Trans>
      </p>
      <a className="inline-block rounded-full bg-primary px-6 py-2.5 text-sm font-semibold text-primary-contrast no-underline hover:opacity-85" href={localePath("/")}>
        <Trans id="notFound.goHome" comment="Link to return to the homepage from a 404 page">
          Go home
        </Trans>
      </a>
    </main>
  );
}
