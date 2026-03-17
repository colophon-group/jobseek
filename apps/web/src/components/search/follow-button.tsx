"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { AlertTriangle } from "lucide-react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { useFollowedCompanies } from "@/components/FollowedCompaniesProvider";

export function FollowButton({ companyId }: { companyId: string }) {
  const { t } = useLingui();
  const { isLoggedIn } = useAuth();
  const lp = useLocalePath();
  const { isFollowed, toggle, isToggling, limitReached } = useFollowedCompanies();

  const followed = isFollowed(companyId);
  const toggling = isToggling(companyId);
  const showWarning = !followed && limitReached;
  const disabled = toggling || showWarning;

  const warningLabel = t({
    id: "plans.followLimit.reached",
    comment: "Tooltip when follow limit is reached",
    message: "You've reached the free plan follow limit",
  });

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    if (!isLoggedIn) {
      window.location.href = lp("/sign-in");
      return;
    }
    if (disabled) return;
    toggle(companyId);
  }

  const button = (
    <button
      onClick={handleClick}
      disabled={disabled}
      aria-label={showWarning ? warningLabel : undefined}
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

  if (!showWarning) return button;

  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          {button}
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="z-50 flex items-center gap-1.5 rounded-md bg-tooltip-warning-bg backdrop-blur-md px-2.5 py-1 text-xs text-white data-[state=delayed-open]:animate-[tooltip-in_150ms_ease] data-[state=instant-open]:animate-[tooltip-in_150ms_ease] data-[state=closed]:animate-[tooltip-out_100ms_ease_forwards]"
            sideOffset={6}
          >
            <AlertTriangle size={12} className="shrink-0" />
            {warningLabel}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
