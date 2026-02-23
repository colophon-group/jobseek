"use client";

import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ContentPageHero } from "@/components/ContentPageHero";

const sectionScroll = "scroll-mt-24 md:scroll-mt-32";

export function TermsContent() {
  const contactEmail = siteConfig.indexing.contactEmail;
  const lastUpdated = siteConfig.terms.lastUpdated;
  const fullTermsLink = `${siteConfig.repoUrl}/blob/main/TERMS-OF-SERVICE`;

  return (
    <main className="py-12 md:py-20">
      <div className="mx-auto max-w-[900px] px-4">
        <div className="flex flex-col gap-12 md:gap-16">
          {/* Hero */}
          <div className={`w-full max-w-[840px] ${sectionScroll}`}>
            <ContentPageHero
              eyebrow={<Trans id="terms.hero.eyebrow" comment="Terms page eyebrow">Terms</Trans>}
              title={<Trans id="terms.hero.title" comment="Terms page title">Terms of Service</Trans>}
              description={<Trans id="terms.hero.description" comment="Terms page description">
                {"By using Job Seek you agree to these terms. Here\u2019s a plain-language overview."}
              </Trans>}
              extra={
                <p className="text-sm text-muted">
                  <Trans id="terms.hero.lastUpdated" comment="Last updated date label">Last updated:</Trans>
                  {" "}{lastUpdated}
                </p>
              }
              artAssetKey={siteConfig.terms.hero.art.assetKey}
              artFocus={siteConfig.terms.hero.art.focus}
            />
          </div>

          {/* The short version */}
          <div className={`w-full max-w-[840px] rounded-lg border border-border-soft bg-surface p-6 md:p-8 ${sectionScroll}`}>
            <h2 className="text-lg font-bold">
              <Trans id="terms.short.title" comment="Short version section title">The short version</Trans>
            </h2>
            <ul className="mt-2 list-disc space-y-1 pl-6">
              <li><Trans id="terms.short.r1" comment="Age requirement">You must be at least 16 to use Job Seek.</Trans></li>
              <li><Trans id="terms.short.r2" comment="What the service does">We aggregate public job postings. We do not guarantee they are accurate or up to date.</Trans></li>
              <li><Trans id="terms.short.r3" comment="No scraping">{"Don\u2019t scrape the service, submit automated applications, or abuse the platform."}</Trans></li>
              <li><Trans id="terms.short.r4" comment="Provided as-is">{"The service is provided as-is \u2014 no warranties."}</Trans></li>
              <li><Trans id="terms.short.r5" comment="Account deletion">You can delete your account at any time.</Trans></li>
            </ul>
          </div>

          {/* Contact + full terms link */}
          <div className={`w-full max-w-[840px] ${sectionScroll}`}>
            <p className="text-muted">
              <Trans id="terms.contact.description" comment="Terms contact call to action">
                Questions? Email us.
              </Trans>
              {" "}
              <a href={`mailto:${contactEmail}`} className="text-primary underline">{contactEmail}</a>
            </p>
            <a href={fullTermsLink} target="_blank" rel="noreferrer" className="mt-2 inline-block font-semibold text-primary underline">
              <Trans id="terms.fullTermsLink" comment="Link to full terms of service text">Read the full Terms of Service</Trans>
              <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
            </a>
          </div>
        </div>
      </div>
    </main>
  );
}
