"use client";

import { useCompanyReferenceErrorMessage } from "@/lib/company-reference-error-message";
import { useId } from "react";
import { useLingui } from "@lingui/react/macro";
import { Star } from "lucide-react";
import { useSession } from "@/components/providers/SessionProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { useStarredCompanies } from "@/components/providers/StarredCompaniesProvider";

export function StarButton({ companyId }: { companyId: string }) {
  const { t } = useLingui();
  const { isLoggedIn, isPending } = useSession();
  const lp = useLocalePath();
  const { isStarred, toggle, isToggling, getError } = useStarredCompanies();

  const errorMessage = useCompanyReferenceErrorMessage();
  const errorId = useId();
  const error = getError(companyId);
  const starred = isStarred(companyId);
  const toggling = isToggling(companyId);

  const label = starred
    ? t({ id: "search.card.starred", comment: "Starred state label for star button on company card", message: "Starred" })
    : t({ id: "search.card.star", comment: "Star button label on company card", message: "Star" });

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    if (isPending) return;
    if (!isLoggedIn) {
      window.location.href = lp("/sign-in");
      return;
    }
    if (toggling) return;
    toggle(companyId);
  }

  return (
    <span className="relative ml-auto inline-flex">
    <button
      onClick={handleClick}
      disabled={toggling}
      aria-label={label}
      aria-describedby={error ? errorId : undefined}
      title={error ? errorMessage(error) : undefined}
      className="cursor-pointer p-1 transition-colors disabled:cursor-default disabled:opacity-50"
    >
      <Star
        size={18}
        aria-hidden="true"
        className={starred ? "fill-accent text-accent" : "text-muted hover:text-accent"}
      />
    </button>
    {error && <span id={errorId} role="alert" className="absolute right-0 top-full z-30 mt-1 w-56 rounded-md border border-border-soft bg-surface p-2 text-xs text-danger shadow-lg">{errorMessage(error)}</span>}
    </span>
  );
}
