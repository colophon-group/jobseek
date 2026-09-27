"use client";

import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ContentPageHero } from "@/components/ContentPageHero";
import { sectionScrollMarginClass as sectionScroll } from "@/lib/styles";

export function PrivacyPolicyContent() {
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
                We collect what we need to provide Job Seek, we don’t sell your data, and you can request access or deletion.
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
              <li><Trans id="privacy.short.r3" comment="Third parties">We use third-party services to operate Job Seek, including sign-in, hosting, storage, and Paddle for Pro payments.</Trans></li>
              <li><Trans id="privacy.short.r4" comment="Cookies">Job Seek uses essential cookies for authentication. Paddle checkout and its payment services operate under Paddle’s privacy notice.</Trans></li>
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
                You can request access, correction, deletion, or export of your data, or object to processing where applicable. Account deletion removes your data from our database within 30 days. Paddle may retain transaction records to meet its legal obligations.
              </Trans>
            </p>
          </div>

          <section className={`w-full max-w-[840px] space-y-4 ${sectionScroll}`}>
            <h2 className="text-lg font-bold"><Trans id="privacy.payments.title" comment="Payment data privacy heading">Pro payments</Trans></h2>
            <p><Trans id="privacy.payments.data" comment="Data exchanged with Paddle and stored by Job Seek">Your email is provided to Paddle at checkout. Paddle collects payment details, billing address, and tax information directly. We store customer and subscription references, trial usage, status, and billing dates to manage your Pro access. We do not store full card numbers or security codes.</Trans></p>
            <p><Trans id="privacy.payments.controller" comment="Paddle independently handles buyer data">As merchant of record, Paddle processes buyer data for payments, taxes, fraud prevention, billing support, and refunds under its own privacy notice. Deleting your Job Seek account does not erase records Paddle must retain.</Trans></p>
            <a href="https://www.paddle.com/legal/privacy" className="inline-block text-primary underline"><Trans id="privacy.payments.notice" comment="Link to Paddle privacy notice">Paddle Privacy Notice</Trans></a>
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
              <Trans id="privacy.extras.fullPolicyLink" comment="Link to full privacy policy text">Read the full Privacy Policy</Trans>
              <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
            </a>
          </div>
        </div>
      </div>
    </main>
  );
}
