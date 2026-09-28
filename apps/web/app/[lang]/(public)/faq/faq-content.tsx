"use client";

import { Trans } from "@lingui/react/macro";
import { ChevronDown } from "lucide-react";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { siteConfig } from "@/content/config";

type FaqItem = { q: string; a: string };
type FaqSection = { id: string; title: string; items: FaqItem[] };

// Native `<details>/<summary>` accordion — no client state, the answer
// paragraph is in the initial HTML for every item. Crawlers / AI fetchers
// that don't execute JavaScript see the full Q&A text without needing a
// `<noscript>` mirror. The chevron rotation uses Tailwind's `group-open`
// modifier to reflect the open state without React.
function FaqItem({ item }: { item: FaqItem }) {
  return (
    <details className="group border-b border-border-soft">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-sm py-4 font-medium transition-colors hover:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary [&::-webkit-details-marker]:hidden">
        <span>{item.q}</span>
        <ChevronDown
          size={18}
          aria-hidden="true"
          className="shrink-0 transition-transform group-open:rotate-180"
        />
      </summary>
      <p className="pb-5 leading-7 text-muted">{item.a}</p>
    </details>
  );
}

export function FaqContent({ sections }: { sections: FaqSection[] }) {
  return (
    <main id="main-content" tabIndex={-1} className="scroll-mt-12 py-12 md:py-20">
      <div className="mx-auto max-w-[720px] px-4">
        <div className="flex flex-col gap-4 text-center">
          <span className={eyebrowClass}>
            <Trans id="faq.eyebrow" comment="FAQ page eyebrow">Support</Trans>
          </span>
          <h1 className={sectionHeadingClass}>
            <Trans id="faq.title" comment="FAQ page heading">Frequently asked questions</Trans>
          </h1>
          <p className="text-muted">
            <Trans id="faq.description" comment="FAQ page description">
              Everything you need to know about Job Seek. Can&apos;t find what you&apos;re looking for? Email us at{" "}
              <a href={`mailto:${siteConfig.indexing.contactEmail}`} className="underline">{siteConfig.indexing.contactEmail}</a>.
            </Trans>
          </p>
        </div>

        <div className="mt-8 flex flex-wrap justify-center gap-2">
          {sections.map(section => <a key={section.id} href={`#${section.id}`} className="rounded-full border border-border-soft px-3 py-2 text-sm transition-colors hover:bg-border-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">{section.title}</a>)}
        </div>
        <div className="mt-10 space-y-10">
          {sections.map(section => <section key={section.id} aria-labelledby={section.id}>
            <h2 id={section.id} className="mb-2 scroll-mt-24 text-xl font-semibold tracking-tight">{section.title}</h2>
            {section.items.map(item => <FaqItem key={item.q} item={item} />)}
          </section>)}
        </div>
      </div>
    </main>
  );
}
