"use client";

import { useState } from "react";
import { Trans } from "@lingui/react/macro";
import { ChevronDown } from "lucide-react";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { siteConfig } from "@/content/config";

type FaqItem = { q: string; a: string };

function FaqAccordion({ item }: { item: FaqItem }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b border-border-soft">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full cursor-pointer items-center justify-between gap-4 py-5 text-left font-medium transition-colors hover:text-muted"
      >
        <span>{item.q}</span>
        <ChevronDown
          size={18}
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <p className="pb-5 text-muted">{item.a}</p>
      )}
    </div>
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
            <FaqAccordion key={i} item={item} />
          ))}
        </div>
      </div>
    </main>
  );
}
