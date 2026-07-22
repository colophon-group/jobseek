import type { ReactNode } from "react";
import { Button } from "@/components/ui/Button";

type CompanyNotFoundStateProps = {
  locale: string;
  slug: string;
  title: ReactNode;
  message: ReactNode;
  exploreLabel: ReactNode;
  requestLabel: ReactNode;
};

/** Shared presentation for both the cached server fallback and client refetch. */
export function CompanyNotFoundState({
  locale,
  slug,
  title,
  message,
  exploreLabel,
  requestLabel,
}: CompanyNotFoundStateProps) {
  const suggestedName = slug.replaceAll("-", " ");
  const requestHref = `/${locale}/companies/request?name=${encodeURIComponent(suggestedName)}`;

  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h1 className="text-2xl font-bold">{title}</h1>
      <p className="mt-2 text-muted">{message}</p>
      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <Button href={`/${locale}/explore`}>{exploreLabel}</Button>
        <Button href={requestHref} variant="outline">
          {requestLabel}
        </Button>
      </div>
    </div>
  );
}
