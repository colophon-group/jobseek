"use client";

import { useState, useEffect } from "react";
import { ChevronUp } from "lucide-react";

/** Floating button that appears after scrolling down, scrolls to top on click. */
export function BackToTop() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 400);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!visible) return null;

  return (
    <button
      type="button"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      aria-label="Back to top"
      className="fixed top-16 right-4 z-40 flex size-10 items-center justify-center rounded-full border border-divider bg-surface-alpha shadow-lg backdrop-blur-md transition-opacity hover:opacity-80 md:top-16 md:right-8"
    >
      <ChevronUp size={20} />
    </button>
  );
}
