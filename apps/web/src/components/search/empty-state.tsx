"use client";

import { Trans } from "@lingui/react/macro";

export function EmptyState() {
  return (
    <h2 className="mb-4 text-lg font-semibold">
      <Trans id="search.empty.heading" comment="Heading shown when no search query is entered">
        Largest companies
      </Trans>
    </h2>
  );
}
