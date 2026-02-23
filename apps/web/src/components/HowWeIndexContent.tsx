"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { TableOfContents } from "@/components/TableOfContents";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { Info } from "lucide-react";

const sectionScroll = "scroll-mt-24 md:scroll-mt-32";

export function HowWeIndexContent() {
  const { t } = useLingui();
  const cfg = siteConfig.indexing;
  const anchors = cfg.anchors;

  const monkArt = publicDomainAssets.the_monk;
  const monkMaxWidth = 320;
  const monkEffectiveWidth = monkArt ? Math.min(monkArt.width, monkMaxWidth) : undefined;
  const monkAspectRatio = monkArt ? monkArt.width / monkArt.height : 1;

  const tocTitle = t({ id: "indexing.toc.title", comment: "Table of contents heading", message: "Contents" });
  const tocAriaLabel = t({ id: "indexing.toc.ariaLabel", comment: "Table of contents aria label", message: "Page contents" });

  const tocItems = [
    { label: t({ id: "indexing.toc.overview", comment: "TOC: Overview", message: "Overview" }), href: `#${anchors.overview}` },
    { label: t({ id: "indexing.toc.assurances", comment: "TOC: Crawling assurances", message: "Crawling assurances" }), href: `#${anchors.assurances}` },
    { label: t({ id: "indexing.toc.ingestion", comment: "TOC: How postings enter the index", message: "How postings enter the index" }), href: `#${anchors.ingestion}` },
    { label: t({ id: "indexing.toc.optOut", comment: "TOC: Opt-out or questions", message: "Opt-out or questions" }), href: `#${anchors.optOut}` },
    { label: t({ id: "indexing.toc.automation", comment: "TOC: Our stance on automation", message: "Our stance on automation" }), href: `#${anchors.automation}` },
    { label: t({ id: "indexing.toc.oss", comment: "TOC: Open-source crawlers", message: "Open-source crawlers" }), href: `#${anchors.oss}` },
    { label: t({ id: "indexing.toc.outreach", comment: "TOC: Need to reach us?", message: "Need to reach us?" }), href: `#${anchors.outreach}` },
  ];

  return (
    <main className="py-12 md:py-20">
      <div className="mx-auto max-w-[1200px] px-4">
        <div className="flex flex-col items-start gap-12 lg:flex-row lg:gap-20">
          <div className="flex flex-1 flex-col gap-12 md:gap-16">
            {/* Overview */}
            <div className={`w-full max-w-[840px] ${sectionScroll}`} id={anchors.overview}>
              <div className="flex flex-col gap-4">
                <span className={eyebrowClass}>
                  <Trans id="indexing.hero.eyebrow" comment="Indexing page eyebrow">Indexing policy</Trans>
                </span>
                <h1 className={sectionHeadingClass}>
                  <Trans id="indexing.hero.title" comment="Indexing page title">How we find and process job postings</Trans>
                </h1>
                <p className="text-muted">
                  <Trans id="indexing.hero.description" comment="Indexing page description">
                    Job Seek is a search engine for both active candidates and professionals who passively track the market—it surfaces fresh roles while staying respectful of employer infrastructure. This page documents exactly how our crawler behaves, the controls that keep it polite, and how jobs ultimately land in the index.
                  </Trans>
                </p>
              </div>
            </div>

            {/* Assurances */}
            <div className={`w-full max-w-[840px] ${sectionScroll}`} id={anchors.assurances}>
              <div className="flex flex-col items-stretch justify-center gap-8 md:flex-row md:items-start md:gap-12">
                <div className="flex-1 rounded-lg border border-border-soft bg-surface p-6 md:order-1 md:p-8">
                  <h2 className="text-lg font-bold">
                    <Trans id="indexing.assurances.title" comment="Assurances section title">Crawling assurances</Trans>
                  </h2>
                  <ul className="mt-4 space-y-4">
                    <li>
                      <p className="font-semibold"><Trans id="indexing.assurances.i1.title" comment="Assurance 1 title">Respectful pacing.</Trans></p>
                      <p className="text-muted"><Trans id="indexing.assurances.i1.body" comment="Assurance 1 body">Every retry window uses exponential backoff so we never hammer an origin, and we bail if a host keeps timing out.</Trans></p>
                    </li>
                    <li>
                      <p className="font-semibold"><Trans id="indexing.assurances.i2.title" comment="Assurance 2 title">Robots, attribution, and TDM reservation.</Trans></p>
                      <p className="text-muted">
                        <Trans id="indexing.assurances.i2.body" comment="Assurance 2 body">
                          Our crawler reads <code>robots.txt</code>, honours disallow rules, identifies itself via <code>User-Agent</code>, and respects the W3C <code>TDM-Reservation</code> header{"\u2014"}if a page signals reservation, we skip it.
                        </Trans>
                      </p>
                    </li>
                    <li>
                      <p className="font-semibold"><Trans id="indexing.assurances.i3.title" comment="Assurance 3 title">One page per minute.</Trans></p>
                      <p className="text-muted"><Trans id="indexing.assurances.i3.body" comment="Assurance 3 body">Even after discovery we retrieve job detail pages at a strict limit of one request per site per minute.</Trans></p>
                    </li>
                  </ul>
                </div>

                {monkArt && monkEffectiveWidth && (
                  <div
                    className="mx-auto flex w-full shrink-0 justify-center md:order-2"
                    style={{
                      flexBasis: monkEffectiveWidth,
                      maxWidth: monkEffectiveWidth,
                      aspectRatio: monkAspectRatio,
                      minHeight: 240,
                    }}
                  >
                    <PublicDomainArt asset={monkArt} credit className="mx-auto h-full w-full" />
                  </div>
                )}
              </div>
            </div>

            {/* Ingestion */}
            <div className={`w-full max-w-[840px] ${sectionScroll}`} id={anchors.ingestion}>
              <h2 className="text-lg font-bold">
                <Trans id="indexing.ingestion.title" comment="Ingestion section title">How postings enter the index</Trans>
              </h2>
              <p className="mt-3 text-muted">
                <Trans id="indexing.ingestion.description" comment="Ingestion section description">
                  We look for structured feeds before scraping raw HTML. First we check for sitemaps, then client-side JSON APIs, and only parse full pages when neither exists.
                </Trans>
              </p>
              <ol className="mt-4 list-decimal space-y-3 pl-6">
                <li>
                  <span className="font-semibold"><Trans id="indexing.ingestion.s1.title" comment="Step 1 title">Sitemap first.</Trans></span>{" "}
                  <span className="text-muted">
                    <Trans id="indexing.ingestion.s1.body" comment="Step 1 body">
                      We look for a sitemap that already lists every careers or job detail page{"\u2014"}ideally linked from <code>robots.txt</code>{"\u2014"}and rely on it whenever possible.
                    </Trans>
                  </span>
                </li>
                <li>
                  <span className="font-semibold"><Trans id="indexing.ingestion.s2.title" comment="Step 2 title">Client APIs second.</Trans></span>{" "}
                  <span className="text-muted">
                    <Trans id="indexing.ingestion.s2.body" comment="Step 2 body">
                      If no sitemap exists we inspect the client application for JSON APIs it calls; when found we hit those endpoints directly to enumerate posting URLs without scraping the DOM.
                    </Trans>
                  </span>
                </li>
                <li>
                  <span className="font-semibold"><Trans id="indexing.ingestion.s3.title" comment="Step 3 title">Graceful page parsing.</Trans></span>{" "}
                  <span className="text-muted">
                    <Trans id="indexing.ingestion.s3.body" comment="Step 3 body">
                      As a last resort we parse the careers pages themselves, preferring newest-first sorts and stopping once previously indexed roles reappear instead of crawling every page.
                    </Trans>
                  </span>
                </li>
                <li>
                  <span className="font-semibold"><Trans id="indexing.ingestion.s4.title" comment="Step 4 title">Selective storage.</Trans></span>{" "}
                  <span className="text-muted">
                    <Trans id="indexing.ingestion.s4.body" comment="Step 4 body">
                      Once we fetch an individual posting we store only the job-specific metadata (title, role description, location, compensation notes, posting URL, and timestamps) plus extracted structured fields. We do not archive unrelated site content.
                    </Trans>
                  </span>
                </li>
              </ol>
            </div>

            {/* Sitemap note */}
            <div className="flex w-full max-w-[840px] items-start gap-3 rounded-md border border-info-border bg-info-bg p-4 text-sm text-info">
              <Info size={18} className="mt-0.5 shrink-0" />
              <p>
                <Trans id="indexing.sitemapNote" comment="Info box about sitemaps">
                  We strongly encourage publishing an easily discoverable sitemap for your careers section. Without it, we periodically mint lightweight <code>HEAD</code> requests against previously discovered job URLs to confirm they are still live, which introduces unnecessary traffic.
                </Trans>
              </p>
            </div>

            {/* Bottom sections */}
            <div className="w-full max-w-[840px] divide-y divide-divider">
              <div className={`pb-8 ${sectionScroll}`} id={anchors.optOut}>
                <h2 className="text-lg font-bold">
                  <Trans id="indexing.optOut.title" comment="Opt-out section title">Opt-out or questions</Trans>
                </h2>
                <p className="mt-3 text-muted">
                  <Trans id="indexing.optOut.body" comment="Opt-out section body">
                    If you notice unexpected activity from our crawler or prefer that your careers site not be indexed, please email us and we will respond promptly.
                  </Trans>
                  {" "}
                  <a href={`mailto:${cfg.contactEmail}`} className="text-primary underline">{cfg.contactEmail}</a>.
                </p>
              </div>

              <div className={`py-8 ${sectionScroll}`} id={anchors.automation}>
                <h2 className="text-lg font-bold">
                  <Trans id="indexing.automation.title" comment="Automation stance title">Our stance on automation</Trans>
                </h2>
                <p className="mt-3 text-muted">
                  <Trans id="indexing.automation.body" comment="Automation stance body">
                    We oppose handing hiring or job-search decisions over to black-box automation {"\u2014"} whether on the employer or applicant side. Every outbound link we share includes <code>utm_source=jobseek</code> so recruiters recognise the traffic, and we continuously review usage patterns plus enforce friction to deter scripted applications.
                  </Trans>
                </p>
              </div>

              <div className={`py-8 ${sectionScroll}`} id={anchors.oss}>
                <h2 className="text-lg font-bold">
                  <Trans id="indexing.oss.title" comment="Open-source section title">Open-source crawlers</Trans>
                </h2>
                <p className="mt-3 text-muted">
                  <Trans id="indexing.oss.body" comment="Open-source section body">
                    Transparency matters, so the code for our job link collection service and extraction pipeline is open source.
                  </Trans>
                  {" "}
                  <Trans id="indexing.oss.browseRepo" comment="Link text for browsing the repo">Browse the repository at</Trans>
                  {" "}
                  <a href={cfg.ossRepoUrl} target="_blank" rel="noreferrer" className="text-primary underline">
                    {cfg.ossRepoUrl.replace("https://", "")}
                    <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
                  </a>.
                </p>
              </div>

              <div className={`pt-8 ${sectionScroll}`} id={anchors.outreach}>
                <h2 className="text-lg font-bold">
                  <Trans id="indexing.outreach.title" comment="Outreach section title">Need to reach us?</Trans>
                </h2>
                <p className="mt-3 text-muted">
                  <Trans id="indexing.outreach.body" comment="Outreach section body">
                    If you notice unusual crawler behaviour, prefer that we do not index your content, or have suggestions on how to improve our safeguards, please reach out.
                  </Trans>
                  {" "}
                  <a href={`mailto:${cfg.contactEmail}`} className="text-primary underline">{cfg.contactEmail}</a>.
                </p>
              </div>
            </div>
          </div>

          <TableOfContents
            title={tocTitle}
            ariaLabel={tocAriaLabel}
            items={tocItems}
            className="hidden max-w-[260px] md:block"
          />
        </div>
      </div>
    </main>
  );
}
