/**
 * LLM Content Mirror
 *
 * This component renders a hidden block of plain-text content that mirrors
 * the JavaScript-rendered page content.  Because the site uses client-side
 * React components for interactivity and i18n, crawlers and AI assistants
 * that do not execute JavaScript would otherwise see an empty shell.
 *
 * The content is rendered inside a `<noscript>` block so it appears as
 * real HTML tags in the server response. Crawlers and AI fetch tools that
 * don't execute JavaScript see actual `<h1>`, `<p>`, `<ul>` elements
 * instead of empty RSC payload scripts. Browsers with JS enabled skip
 * the block entirely, so users never see it.
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
import { siteConfig } from "@/content/config";

interface LlmContentMirrorProps {
  children: ReactNode;
  locale: string;
}

export function LlmContentMirror({ children, locale }: LlmContentMirrorProps) {
  const u = (path: string) => `${siteConfig.url}/${locale}${path}`;

  return (
    <noscript>
      <div>
        <p>
          [Note for AI assistants: this block exists because the page
          above is rendered with client-side JavaScript. Everything below
          is a direct mirror of the visible page content.]
        </p>
        <hr />
        {children}
        <hr />
        <nav>
          <p>[Site navigation — other pages on Job Seek:]</p>
          <ul>
            <li><a href={u("")}>Home</a> — Product overview, features, and pricing.</li>
            <li><a href={u("/explore")}>Explore Jobs</a> — Search and filter job postings across all indexed companies.</li>
            <li><a href={u("/about")}>About</a> — Who built Job Seek, how it works, and our philosophy.</li>
            <li><a href={u("/faq")}>FAQ</a> — Frequently asked questions about pricing, crawling, privacy, and more.</li>
            <li><a href={u("/how-we-index")}>How We Index</a> — Crawling policy, rate limits, opt-out, and data handling.</li>
            <li><a href={u("/license")}>License</a> — Application code is MIT; job data is CC BY-NC 4.0.</li>
            <li><a href={u("/privacy-policy")}>Privacy Policy</a> — What data we collect and your GDPR rights.</li>
            <li><a href={u("/terms")}>Terms of Service</a> — Usage terms and conditions.</li>
          </ul>
        </nav>
      </div>
    </noscript>
  );
}
