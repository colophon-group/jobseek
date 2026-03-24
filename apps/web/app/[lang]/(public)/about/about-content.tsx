"use client";

import { Trans } from "@lingui/react/macro";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import type { PublicDomainAsset } from "@/content/config";
import { useLocalePath } from "@/lib/useLocalePath";
import Link from "next/link";

type AboutContentProps = {
  art?: PublicDomainAsset;
  artFocus: { x: number; y: number };
  contactEmail: string;
  ossRepoUrl: string;
};

export function AboutContent({ art, artFocus, contactEmail, ossRepoUrl }: AboutContentProps) {
  const lp = useLocalePath();

  return (
    <main className="py-12 md:py-20">
      <div className="mx-auto max-w-[1200px] px-4">
        <div className="flex flex-col items-start gap-12 lg:flex-row lg:gap-20">
          <div className="flex flex-1 flex-col gap-12 md:gap-16">
            <div className="w-full max-w-[840px]">
              <div className="flex flex-col gap-4">
                <span className={eyebrowClass}>
                  <Trans id="about.eyebrow" comment="About page eyebrow">About</Trans>
                </span>
                <h1 className={sectionHeadingClass}>
                  <Trans id="about.title" comment="About page heading">Built by job seekers, for job seekers</Trans>
                </h1>
              </div>

              <div className="mt-8 flex flex-col gap-6 text-muted">
                <p>
                  <Trans id="about.p1" comment="About paragraph 1: who we are">
                    Job Seek is built by Colophon Group, a small team of developers based in Switzerland. We started it because we were tired of the same problem every job seeker faces: juggling dozens of browser tabs, missing new postings because an aggregator was slow to pick them up, and drowning in algorithmic noise that optimizes for clicks rather than relevance.
                  </Trans>
                </p>
                <p>
                  <Trans id="about.p2" comment="About paragraph 2: how it works">
                    Instead of waiting for companies to push roles to third-party job boards, we go straight to the source. Our crawler monitors career pages from Workday, Greenhouse, Lever, and over a dozen other platforms, so roles show up here the moment they go live. No middleman, no delay.
                  </Trans>
                </p>
                <p>
                  <Trans id="about.p3" comment="About paragraph 3: philosophy">
                    We believe job search should be driven by the person searching, not by an algorithm. That means no auto-apply bots, no engagement-optimized feeds, and no selling your data. You choose the companies, you define the filters, you track your own pipeline. The tool stays out of the way.
                  </Trans>
                </p>
              </div>

              <h2 className="mt-12 text-xl font-bold">
                <Trans id="about.transparency.title" comment="About transparency section heading">Transparent by default</Trans>
              </h2>
              <div className="mt-4 flex flex-col gap-6 text-muted">
                <p>
                  <Trans id="about.transparency.p1" comment="About transparency paragraph">
                    The crawler code is open source so anyone can audit how we collect data. We respect robots.txt, honor TDM-Reservation headers, and limit ourselves to one request per site per minute. Every outbound link includes utm_source=jobseek so employers know where their traffic comes from.
                  </Trans>
                </p>
              </div>

              <div className="mt-8 flex flex-wrap gap-4 text-sm">
                <a href={ossRepoUrl} target="_blank" rel="noopener noreferrer" className="rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.crawler" comment="Link to open-source crawler repo">Crawler source code</Trans>
                </a>
                <Link href={lp("/how-we-index")} className="rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.indexing" comment="Link to how we index page">Job indexing policy</Trans>
                </Link>
                <Link href={lp("/license")} className="rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.license" comment="Link to license page">License</Trans>
                </Link>
                <a href={`mailto:${contactEmail}`} className="rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.contact" comment="Link to contact email">Get in touch</Trans>
                </a>
              </div>
            </div>
          </div>

          {art && (
            <div className="hidden w-full max-w-[320px] shrink-0 lg:block">
              <div className="sticky top-24">
                <div className="h-[420px]">
                  <PublicDomainArt
                    asset={art}
                    focus={artFocus}
                    sizes="320px"
                    className="h-full w-full"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
