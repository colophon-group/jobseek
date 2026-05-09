"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import { Trans } from "@lingui/react/macro";
import { tooltipClass } from "@/components/ui/tooltip-styles";

interface DisabledFilterPillProps {
  /** Display name (e.g. "Berlin"). */
  name: string;
  /** Active posting count for the row. Rendered as `(count)`. */
  count?: number;
  /** Localized name of the ancestor causing the disable. */
  ancestorName: string;
  /**
   * Variant — `pill` (default rounded chip), `parent` (sub-group parent
   * header inside the occupation modal), `country` (uppercase country
   * header in the location modal), or `region` (region sub-header).
   * Each variant matches the visual treatment of its enabled counterpart
   * in the original modal so the disabled state reads as a state
   * transition rather than a different element.
   */
  variant?: "pill" | "parent" | "country" | "region" | "domain";
  /** Optional left-side icon (e.g. country flag). Only rendered for `country` variant. */
  leftAdornment?: React.ReactNode;
  /** Auxiliary count rendered after the name (e.g. parent + children sum). */
  auxText?: string;
}

/**
 * Greyed, non-interactive pill rendered when an ancestor filter
 * subsumes this row. Conveys "you can't add this — it's already
 * implied" via opacity, tooltip, and aria-disabled. Click is a no-op.
 *
 * Hierarchical filter UX (#2978).
 */
export function DisabledFilterPill({
  name,
  count,
  ancestorName,
  variant = "pill",
  leftAdornment,
  auxText,
}: DisabledFilterPillProps) {
  const baseClass = (() => {
    switch (variant) {
      case "country":
        return "mb-2 cursor-not-allowed text-xs font-semibold uppercase tracking-wider text-muted opacity-50";
      case "region":
        return "mb-1.5 cursor-not-allowed text-xs font-medium text-muted opacity-50";
      case "domain":
        return "cursor-not-allowed text-xs font-semibold uppercase tracking-wider text-muted opacity-50";
      case "parent":
        return "mb-1.5 cursor-not-allowed text-sm font-medium text-foreground opacity-50";
      case "pill":
      default:
        return "inline-flex cursor-not-allowed items-center gap-1 rounded-full border border-border-soft px-3 py-1 text-sm text-muted opacity-50";
    }
  })();

  return (
    <Tooltip.Provider delayDuration={150}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            type="button"
            aria-disabled
            tabIndex={-1}
            onClick={(e) => e.preventDefault()}
            className={baseClass}
          >
            {leftAdornment}
            <span>{name}</span>
            {auxText && (
              <span className="ml-1 text-xs font-normal text-muted">{auxText}</span>
            )}
            {count != null && variant === "pill" && (
              <span className="text-xs text-muted">({count})</span>
            )}
            {count != null && (variant === "country" || variant === "region" || variant === "domain") && (
              <span className="ml-1 text-[10px] font-normal normal-case text-muted">({count})</span>
            )}
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className={tooltipClass} sideOffset={6}>
            <Trans
              id="search.filterModal.includedInAncestor"
              comment="Tooltip on a disabled filter pill explaining the row is implied by an already-selected ancestor (e.g. 'Included in European Union' when EU is selected)."
            >
              Included in {ancestorName}
            </Trans>
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
