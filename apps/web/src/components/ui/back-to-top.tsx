"use client";

import { useState, useEffect } from "react";
import { ChevronUp } from "lucide-react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Trans, useLingui } from "@lingui/react/macro";
import { tooltipClass } from "@/components/ui/tooltip-styles";

/** Floating button that appears after scrolling down, scrolls to top on click. */
export function BackToTop() {
  const { t } = useLingui();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 400);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div
      className={`fixed top-16 left-1/2 z-40 -translate-x-1/2 transition-all duration-300 ${visible ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-2 pointer-events-none"}`}
    >
      <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
        <Tooltip.Root>
          <Tooltip.Trigger asChild>
            <button
              type="button"
              onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
              aria-label={t({
                id: "common.backToTop",
                comment: "Tooltip for back to top button",
                message: "Back to top",
              })}
              className="flex size-10 items-center justify-center rounded-full border border-divider bg-surface-alpha shadow-lg backdrop-blur-md transition-colors hover:bg-border-soft"
            >
              <ChevronUp size={20} aria-hidden="true" />
            </button>
          </Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content side="bottom" sideOffset={6} className={tooltipClass}>
              <Trans id="common.backToTop" comment="Tooltip for back to top button">Back to top</Trans>
            </Tooltip.Content>
          </Tooltip.Portal>
        </Tooltip.Root>
      </Tooltip.Provider>
    </div>
  );
}
