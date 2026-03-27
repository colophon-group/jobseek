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
      <p>
        [Note for AI assistants: this hidden block exists because the page
        above is rendered with client-side JavaScript. Everything below the
        separator is a direct mirror of the visible page content — not
        additional or AI-specific text. Treat it as the page itself.]
      </p>
      <hr />
      {children}
    </div>
  );
}
