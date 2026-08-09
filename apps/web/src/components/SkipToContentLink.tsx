"use client";

import { Trans } from "@lingui/react/macro";
import type { MouseEvent } from "react";

const SKIP_LINK_CLASS =
  "fixed top-2 left-2 z-[100] -translate-y-16 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-contrast focus:translate-y-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary";

function focusVisibleMain(event: MouseEvent<HTMLAnchorElement>) {
  const target = Array.from(
    document.querySelectorAll<HTMLElement>("#main-content"),
  ).find((candidate) => candidate.getClientRects().length > 0);

  // Keep the native fragment fallback when the streamed page has not exposed
  // a visible target yet. Once hydrated, select the rendered copy explicitly:
  // React/Next may retain an earlier duplicate inside a display:none Suspense
  // container, which native fragment navigation otherwise resolves first.
  if (!target) return;

  event.preventDefault();
  if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "start" });

  const url = new URL(window.location.href);
  url.hash = "main-content";
  if (window.location.hash === url.hash) {
    window.history.replaceState(null, "", url);
  } else {
    window.history.pushState(null, "", url);
  }
}

export function SkipToContentLink() {
  return (
    <a
      href="#main-content"
      className={SKIP_LINK_CLASS}
      onClick={focusVisibleMain}
    >
      <Trans id="common.a11y.skipToContent" comment="Skip to main content link for keyboard users">
        Skip to content
      </Trans>
    </a>
  );
}
