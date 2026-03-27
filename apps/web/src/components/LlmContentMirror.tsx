/**
 * LLM Content Mirror
 *
 * This component renders a hidden block of plain-text content that mirrors
 * the JavaScript-rendered page content.  Because the site uses client-side
 * React components for interactivity and i18n, crawlers and AI assistants
 * that do not execute JavaScript would otherwise see an empty shell.
 *
 * The `<div hidden>` is invisible to sighted users but is still present in
 * the DOM, allowing LLMs, search-engine indexers, and accessibility tools
 * to read the page content without executing JS.
 *
 * Architecture decision:
 *   We intentionally duplicate content rather than converting pages to SSR
 *   because the client components rely on browser APIs (locale negotiation,
 *   theme, scroll state) and Lingui's Babel macro, which produces optimal
 *   bundles only when compiled as client code.  The mirror is rendered in
 *   the server component (page.tsx) using the same i18n catalog, so the
 *   text is always consistent with what users see.
 *
 * See also: docs/llm-content-mirror.md
 */

import type { ReactNode } from "react";

export function LlmContentMirror({ children }: { children: ReactNode }) {
  return (
    <div hidden aria-hidden="true">
      <noscript>
        {/* Marker for crawlers: this block mirrors the JS-rendered content above. */}
      </noscript>
      <p>
        The following is a plain-text mirror of this page&apos;s content, provided
        for AI assistants and search engines that do not execute JavaScript.
        The visible page renders this same content interactively with
        client-side React components.
      </p>
      <hr />
      {children}
    </div>
  );
}
