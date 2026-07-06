"use client";

import { Trans } from "@lingui/react/macro";
import { ChevronDown } from "lucide-react";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { siteConfig } from "@/content/config";

type FaqItem = { q: string; a: string };

// Native `<details>/<summary>` accordion — no client state, the answer
// paragraph is in the initial HTML for every item. Crawlers / AI fetchers
// that don't execute JavaScript see the full Q&A text without needing a
// `<noscript>` mirror. The chevron rotation uses Tailwind's `group-open`
// modifier to reflect the open state without React.
function FaqItem({ item }: { item: FaqItem }) {
  return (
    <details className="group border-b border-border-soft">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-5 font-medium transition-colors hover:text-muted [&::-webkit-details-marker]:hidden">
        <span>{item.q}</span>
        <ChevronDown
          size={18}
          className="shrink-0 transition-transform group-open:rotate-180"
        />
      </summary>
      <p className="pb-5 text-muted">{item.a}</p>
    </details>
  );
}

export function FaqContent({ items }: { items: FaqItem[] }) {
  return (
    <main className="py-12 md:py-20">
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

        <div className="mt-12">
          {items.map((item, i) => (
            <FaqItem key={i} item={item} />
          ))}
        </div>
      </div>
    </main>
  );
}
