"use client";

import { Trans } from "@lingui/react/macro";
import { useParams } from "next/navigation";
import { useSession } from "@/components/providers/SessionProvider";

export function TruncationPrompt({ type }: { type: "companies" | "postings" }) {
  const { isPending } = useSession();
  const params = useParams();
  const lang = (params.lang as string) ?? "en";

  if (isPending) return null;

  return (
    <div className="py-4">
      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-divider" />
        <a
          href={`/${lang}/sign-in`}
          className="whitespace-nowrap rounded-full border border-primary bg-primary px-4 py-1.5 text-xs font-semibold text-primary-contrast transition-opacity hover:opacity-90"
        >
          {type === "companies" ? (
            <Trans
              id="truncation.companies.title"
              comment="Prompt shown when anonymous user hits the company pagination limit"
            >
              Sign in to see more companies
            </Trans>
          ) : (
            <Trans
              id="truncation.postings.title"
              comment="Prompt shown when anonymous user hits the posting pagination limit"
            >
              Sign in to see more job postings
            </Trans>
          )}
        </a>
        <div className="h-px flex-1 bg-divider" />
      </div>
      <p className="mt-3 text-center text-xs text-muted">
        <Trans
          id="truncation.benefits"
          comment="Description of benefits shown below the sign-in prompt for anonymous users"
        >
          Create a free account to browse all results, save jobs, track applications, build watchlists, and get alerts for new openings.
        </Trans>
      </p>
    </div>
  );
}
