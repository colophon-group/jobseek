"use client";

import { Trans } from "@lingui/react/macro";
import { ContentPageHero } from "@/components/ContentPageHero";
import { siteConfig } from "@/content/config";
import { useLocalePath } from "@/lib/useLocalePath";
import Link from "next/link";
import { ExternalLink } from "lucide-react";

type AboutContentProps = {
  contactEmail: string;
  ossRepoUrl: string;
};

export function AboutContent({ contactEmail, ossRepoUrl }: AboutContentProps) {
  const lp = useLocalePath();

  return (
    <main className="py-12 md:py-20">
      <div className="mx-auto max-w-[900px] px-4">
        <div className="flex flex-col gap-12 md:gap-16">
          <div className="w-full max-w-[840px]">
              <ContentPageHero
                eyebrow={<Trans id="about.eyebrow" comment="About page eyebrow">About</Trans>}
                title={<Trans id="about.title" comment="About page heading">Built by job seekers, for job seekers</Trans>}
                description={<Trans id="about.p1" comment="About paragraph 1: who we are">
                  Job Seek is built by Colophon Group, a small team of developers based in Switzerland. We started it because we were tired of the same problem every job seeker faces: juggling dozens of browser tabs, missing new postings because an aggregator was slow to pick them up, and drowning in algorithmic noise that optimizes for clicks rather than relevance.
                </Trans>}
                artAssetKey={siteConfig.about.hero.art.assetKey}
                artFocus={siteConfig.about.hero.art.focus}
                artMaxWidth={320}
              />
            </div>

            <div className="w-full max-w-[840px]">
              <div className="flex flex-col gap-6 text-muted">
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
                <a href={ossRepoUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.crawler" comment="Link to open-source crawler repo">Crawler source code</Trans>
                  <ExternalLink size={14} />
                </a>
                <Link href={lp("/how-we-index")} className="inline-flex items-center gap-1.5 rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.indexing" comment="Link to how we index page">Job indexing policy</Trans>
                  <ExternalLink size={14} />
                </Link>
                <Link href={lp("/license")} className="inline-flex items-center gap-1.5 rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.license" comment="Link to license page">License</Trans>
                  <ExternalLink size={14} />
                </Link>
                <a href={`mailto:${contactEmail}`} className="inline-flex items-center gap-1.5 rounded-full border border-border-soft px-4 py-2 transition-colors hover:bg-border-soft">
                  <Trans id="about.link.contact" comment="Link to contact email">Get in touch</Trans>
                  <ExternalLink size={14} />
                </a>
              </div>
            </div>
        </div>
      </div>
    </main>
  );
}
