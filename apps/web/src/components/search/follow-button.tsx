"use client";

import { Trans } from "@lingui/react/macro";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { useFollowedCompanies } from "@/components/FollowedCompaniesProvider";

export function FollowButton({ companyId }: { companyId: string }) {
  const { isLoggedIn } = useAuth();
  const lp = useLocalePath();
  const { isFollowed, toggle, isToggling } = useFollowedCompanies();

  const followed = isFollowed(companyId);
  const toggling = isToggling(companyId);

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    if (!isLoggedIn) {
      window.location.href = lp("/sign-in");
      return;
    }
    toggle(companyId);
  }

  return (
    <button
      onClick={handleClick}
      disabled={toggling}
      className={`ml-auto rounded-full border px-3 py-0.5 text-xs cursor-pointer transition-colors disabled:cursor-default disabled:opacity-50 ${
        followed
          ? "border-accent bg-accent/10 text-accent"
          : "border-border-soft text-muted hover:border-accent hover:text-accent"
      }`}
    >
      {followed ? (
        <Trans id="search.card.following" comment="Following button on company card (user is following this company)">
          Following
        </Trans>
      ) : (
        <Trans id="search.card.follow" comment="Follow button on company card">
          Follow
        </Trans>
      )}
    </button>
  );
}
