"use client";

import { Trans } from "@lingui/react/macro";

export function SearchUnavailable() {
  return (
    <div role="alert" className="flex flex-col items-center gap-2 py-12 text-center">
      <p className="text-lg font-semibold">
        <Trans id="search.unavailable.heading" comment="Heading shown when a search surface returns an impossible empty result set">
          Oops, something went wrong.
        </Trans>
      </p>
      <p className="text-sm text-muted">
        <Trans id="search.unavailable.body" comment="Body shown when a search surface returns an impossible empty result set">
          Try refreshing the page.
        </Trans>
      </p>
    </div>
  );
}
