"use client";

import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ContentPageHero } from "@/components/ContentPageHero";

const sectionScroll = "scroll-mt-24 md:scroll-mt-32";

export function PrivacyPolicyContent() {
  const contactEmail = siteConfig.indexing.contactEmail;
  const lastUpdated = siteConfig.privacy.lastUpdated;
  const fullPolicyLink = `${siteConfig.repoUrl}/blob/main/PRIVACY-POLICY`;

  return (
    <main className="py-12 md:py-20">
      <div className="mx-auto max-w-[900px] px-4">
        <div className="flex flex-col gap-12 md:gap-16">
          {/* Hero */}
          <div className={`w-full max-w-[840px] ${sectionScroll}`}>
            <ContentPageHero
              eyebrow={<Trans id="privacy.hero.eyebrow" comment="Privacy policy page eyebrow">Privacy</Trans>}
              title={<Trans id="privacy.hero.title" comment="Privacy policy page title">Privacy at Job Seek</Trans>}
              description={<Trans id="privacy.hero.description" comment="Privacy policy page description">
                {"We collect only what we need, we don\u2019t sell your data, and you can delete everything at any time."}
              </Trans>}
              extra={
                <p className="text-sm text-muted">
                  <Trans id="privacy.hero.lastUpdated" comment="Last updated date label">Last updated:</Trans>
                  {" "}{lastUpdated}
                </p>
              }
              artAssetKey={siteConfig.privacy.hero.art.assetKey}
              artFocus={siteConfig.privacy.hero.art.focus}
            />
          </div>

          {/* The short version */}
          <div className={`w-full max-w-[840px] rounded-lg border border-border-soft bg-surface p-6 md:p-8 ${sectionScroll}`}>
            <h2 className="text-lg font-bold">
              <Trans id="privacy.short.title" comment="Short version section title">The short version</Trans>
            </h2>
            <ul className="mt-2 list-disc space-y-1 pl-6">
              <li><Trans id="privacy.short.r1" comment="What we store">We store your name, email, and profile picture from your OAuth sign-in, plus the data you create while using the app.</Trans></li>
              <li><Trans id="privacy.short.r2" comment="No selling">{"We don\u2019t sell, rent, or share your data for marketing."}</Trans></li>
              <li><Trans id="privacy.short.r3" comment="Third parties">{"We use a handful of third-party services for sign-in, hosting, and storage \u2014 nothing else."}</Trans></li>
              <li><Trans id="privacy.short.r4" comment="Cookies">{"Cookies are session-only \u2014 no ads, no tracking."}</Trans></li>
              <li><Trans id="privacy.short.r5" comment="Encryption">All data is encrypted in transit and at rest.</Trans></li>
            </ul>
          </div>

          {/* Your rights */}
          <div className={`w-full max-w-[840px] rounded-lg border border-border-soft bg-surface p-6 md:p-8 ${sectionScroll}`}>
            <h2 className="text-lg font-bold">
              <Trans id="privacy.rights.title" comment="Your rights section title">Your rights</Trans>
            </h2>
            <p className="mt-2 text-muted">
              <Trans id="privacy.rights.intro" comment="Your rights intro">
                {"Under GDPR you can ask for a copy of your data, have it corrected or deleted, export it, or object to processing. Delete your account and everything is wiped within 30 days."}
              </Trans>
            </p>
          </div>

          {/* Contact + full policy link */}
          <div className={`w-full max-w-[840px] ${sectionScroll}`}>
            <p className="text-muted">
              <Trans id="privacy.contact.description" comment="Privacy contact call to action">
                Questions? Email us.
              </Trans>
              {" "}
              <a href={`mailto:${contactEmail}`} className="text-primary underline">{contactEmail}</a>
            </p>
            <a href={fullPolicyLink} target="_blank" rel="noreferrer" className="mt-2 inline-block font-semibold text-primary underline">
              <Trans id="privacy.extras.fullPolicyLink" comment="Link to full privacy policy text">Read the full Privacy Policy</Trans>
              <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
            </a>
          </div>
        </div>
      </div>
    </main>
  );
}
