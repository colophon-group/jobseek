"use client";

import type { ReactNode } from "react";
import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ContentPageHero } from "@/components/ContentPageHero";
import { sectionScrollMarginClass as sectionScroll } from "@/lib/styles";

export function PrivacyPolicyContent({ children }: { children: ReactNode }) {
  const contactEmail = siteConfig.indexing.contactEmail;
  const lastUpdated = siteConfig.privacy.lastUpdated;
  const fullPolicyLink = `${siteConfig.repoUrl}/blob/main/PRIVACY-POLICY`;

  return (
    <main id="main-content" tabIndex={-1} className="scroll-mt-12 py-12 md:py-20">
      <div className="mx-auto max-w-[900px] px-4">
        <div className="flex flex-col gap-12 md:gap-16">
          {/* Hero */}
          <div className={`w-full max-w-[840px] ${sectionScroll}`}>
            <ContentPageHero
              eyebrow={<Trans id="privacy.hero.eyebrow" comment="Privacy policy page eyebrow">Privacy</Trans>}
              title={<Trans id="privacy.hero.title" comment="Privacy policy page title">Privacy at Job Seek</Trans>}
              description={<Trans id="privacy.hero.description" comment="Privacy policy page description">
                How we use your data, the providers involved, and your choices and rights.
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
              <li><Trans id="privacy.short.r1" comment="What we store">We store account details and the data you create. Email/password accounts store a password hash; external sign-in does not give us your provider password.</Trans></li>
              <li><Trans id="privacy.short.r2" comment="No selling">{"We don\u2019t sell, rent, or share your data for marketing."}</Trans></li>
              <li><Trans id="privacy.short.r3" comment="Third parties">Providers help us host the service, send email, interpret searches, filter job descriptions and handle payments. The full policy names them and explains international processing.</Trans></li>
              <li><Trans id="privacy.short.r4" comment="Cookies">We use necessary sign-in cookies and preference storage, some of which persist between visits. We do not use advertising cookies.</Trans></li>
              <li><Trans id="privacy.short.r5" comment="Encryption">We use HTTPS, password hashing, restricted system access and encrypted recovery backups.</Trans></li>
            </ul>
          </div>

          <address className="not-italic">Viktor Shcherbakov — Job Seek<br />Route d'Oron 5<br />1010 Lausanne<br /><Trans id="legal.contact.country" comment="Country in the public seller and controller address">Switzerland</Trans></address>

          {/* Your rights */}
          <div className={`w-full max-w-[840px] rounded-lg border border-border-soft bg-surface p-6 md:p-8 ${sectionScroll}`}>
            <h2 className="text-lg font-bold">
              <Trans id="privacy.rights.title" comment="Your rights section title">Your rights</Trans>
            </h2>
            <p className="mt-2 text-muted">
              <Trans id="privacy.rights.intro" comment="Your rights intro">
                Swiss data protection law and, where applicable, the EU or UK GDPR give you rights over your data. Account deletion removes active account records; recovery backups and records held by providers have separate retention periods, explained below.
              </Trans>
            </p>
          </div>

          <section id="full-privacy" className={`w-full max-w-[840px] space-y-4 ${sectionScroll}`}>
            <h2 className="text-lg font-bold"><Trans id="privacy.complete.title" comment="Heading identifying the full English legal document below the translated summary">Complete Privacy Policy (English)</Trans></h2>
            {children}
          </section>

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
              <Trans id="privacy.extras.fullPolicyLink" comment="Link to full privacy policy text">Privacy Policy on GitHub</Trans>
              <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
            </a>
          </div>
        </div>
      </div>
    </main>
  );
}
