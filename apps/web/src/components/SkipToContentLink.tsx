"use client";

import { Trans } from "@lingui/react/macro";

const SKIP_LINK_CLASS =
  "sr-only focus:not-sr-only fixed top-2 left-2 z-[100] rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-contrast focus:outline-none";

export function SkipToContentLink() {
  return (
    <a href="#main-content" className={SKIP_LINK_CLASS}>
      <Trans id="common.a11y.skipToContent" comment="Skip to main content link for keyboard users">
        Skip to content
      </Trans>
    </a>
  );
}
