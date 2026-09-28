"use client";

import type { MouseEvent, ReactNode } from "react";

export function ArticleJumpLink({ href, children }: { href: `#${string}`; children: ReactNode }) {
  function jumpToSection(event: MouseEvent<HTMLAnchorElement>) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    // PPR can retain a hidden copy of the article with duplicate fragment IDs.
    // Resolve within the clicked link's article rather than the whole document.
    const target = event.currentTarget.closest("main")?.querySelector<HTMLElement>(
      `#${CSS.escape(href.slice(1))}`,
    );
    if (!target) return;

    event.preventDefault();
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "start" });
    if (window.location.hash !== href) window.history.pushState(null, "", href);
  }

  return <a href={href} onClick={jumpToSection}>{children}</a>;
}
