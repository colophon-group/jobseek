"use client";

import { useLingui } from "@lingui/react/macro";
import { Star } from "lucide-react";
import { useSession } from "@/components/SessionProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { useStarredCompanies } from "@/components/StarredCompaniesProvider";

export function StarButton({ companyId }: { companyId: string }) {
  const { t } = useLingui();
  const { isLoggedIn, isPending } = useSession();
  const lp = useLocalePath();
  const { isStarred, toggle, isToggling } = useStarredCompanies();

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
    <button
      onClick={handleClick}
      disabled={toggling}
      aria-label={label}
      className="ml-auto cursor-pointer p-1 transition-colors disabled:cursor-default disabled:opacity-50"
    >
      <Star
        size={18}
        aria-hidden="true"
        className={starred ? "fill-accent text-accent" : "text-muted hover:text-accent"}
      />
    </button>
  );
}
