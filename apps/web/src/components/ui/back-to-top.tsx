"use client";

import { useState, useEffect } from "react";
import { ChevronUp } from "lucide-react";
import { Trans } from "@lingui/react/macro";

/** Floating pill button that appears after scrolling down, scrolls to top on click. */
export function BackToTop() {
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
      <button
        type="button"
        onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        aria-label="Back to top"
        className="flex h-8 items-center gap-1.5 rounded-full border border-divider bg-surface-alpha px-3 shadow-lg backdrop-blur-md transition-colors hover:bg-border-soft"
      >
        <ChevronUp size={14} />
        <span className="text-xs font-medium">
          <Trans id="common.backToTop" comment="Back to top button label">Back to top</Trans>
        </span>
      </button>
    </div>
  );
}
