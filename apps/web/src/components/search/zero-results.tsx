"use client";

import { Trans } from "@lingui/react/macro";
import { RequestCompanyPrompt } from "./request-company";

interface ZeroResultsProps {
  query: string;
}

export function ZeroResults({ query }: ZeroResultsProps) {
  return (
    <div className="flex flex-col items-center gap-4 py-12 text-center">
      <p className="text-lg font-semibold">
        <Trans id="search.zero.heading" comment="Heading when search returns no results">
          No results found for &ldquo;{query}&rdquo;
        </Trans>
      </p>
      <RequestCompanyPrompt />
    </div>
  );
}
