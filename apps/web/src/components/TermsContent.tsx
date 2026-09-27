"use client";

import type { ReactNode } from "react";
import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ContentPageHero } from "@/components/ContentPageHero";
import { sectionScrollMarginClass as sectionScroll } from "@/lib/styles";
import { useLocalePath } from "@/lib/useLocalePath";

export function TermsContent({ children }: { children: ReactNode }) {
  const lp = useLocalePath();
  const contactEmail = siteConfig.indexing.contactEmail;
  const lastUpdated = siteConfig.terms.lastUpdated;
  const fullTermsLink = `${siteConfig.repoUrl}/blob/main/TERMS-OF-SERVICE`;

  return (
    <main id="main-content" tabIndex={-1} className="scroll-mt-12 py-12 md:py-20">
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
              <li><Trans id="terms.short.r4" comment="Provided as-is">The service has limitations. Your mandatory consumer rights remain protected.</Trans></li>
              <li><Trans id="terms.short.r5" comment="Account deletion">You can delete your account at any time.</Trans></li>
            </ul>
          </div>

          <section className={`w-full max-w-[840px] space-y-4 ${sectionScroll}`}>
            <h2 className="text-lg font-bold"><Trans id="terms.billing.title" comment="Paid subscription terms heading">Pro subscriptions and payments</Trans></h2>
            <p><Trans id="terms.billing.seller" comment="Identifies the service operator and merchant of record">Job Seek is operated by Viktor Shcherbakov in Switzerland. For purchases through Paddle, Paddle acts as reseller and merchant of record, handling payments, applicable taxes, billing support, and refunds.</Trans></p>
            <address className="not-italic">Viktor Shcherbakov<br />Route d'Oron 5<br />1010 Lausanne<br /><Trans id="legal.contact.country" comment="Country in the public seller and controller address">Switzerland</Trans></address>
            <p><Trans id="terms.billing.offer" comment="Subscription scope and recurring trial terms">Pro adds Narrowed results for watchlists. Eligible accounts receive seven days free, then pay US$10 per month plus applicable taxes shown at checkout. A payment method is required. Subscriptions renew automatically until canceled; returning subscribers do not receive another trial.</Trans></p>
            <p><Trans id="terms.billing.cancel" comment="How and when subscription cancellation takes effect">Cancel before the trial ends to avoid the first charge. Use Settings → Subscription → Manage subscription, the link in your receipt, or Paddle support. Normal cancellation preserves access until the end shown in your subscription settings. Deleting your account cancels billing and ends access immediately.</Trans></p>
            <p><a href={lp("/settings/billing")} className="text-primary underline"><Trans id="settings.billing.manage" comment="Manage subscription button label">Manage subscription</Trans></a>{" · "}<a href="https://www.paddle.com/legal/buyer-terms" className="text-primary underline"><Trans id="terms.billing.buyerTerms" comment="Link to Paddle buyer terms">Paddle Buyer Terms</Trans></a></p>
          </section>
          <section id="refund-policy" className={`w-full max-w-[840px] space-y-4 ${sectionScroll}`}>
            <h2 className="text-lg font-bold"><Trans id="terms.refunds.title" comment="Public refund policy heading">Refund policy</Trans></h2>
            <p><Trans id="terms.refunds.request" comment="Refund request process without restricting statutory rights">Request a refund or exercise an applicable withdrawal right through Paddle support or the support link in your receipt. Paddle assesses and processes requests under its Refund Policy. Your mandatory consumer rights are unaffected.</Trans></p>
            <p><Trans id="terms.refunds.cancel" comment="Explains cancellation is separate from requesting a refund">Canceling a subscription stops future renewals; it does not automatically request a refund. For a problem with Job Seek, contact us at the email below so we can help.</Trans></p>
            <p><a href="https://paddle.net" className="text-primary underline"><Trans id="terms.refunds.support" comment="Link to Paddle buyer support and refunds">Paddle support and refunds</Trans></a>{" · "}<a href="https://www.paddle.com/legal/refund-policy" className="text-primary underline"><Trans id="terms.refunds.policy" comment="Link to Paddle refund policy">Paddle Refund Policy</Trans></a></p>
          </section>

          <section id="full-terms" className={`w-full max-w-[840px] space-y-4 ${sectionScroll}`}>
            <h2 className="text-lg font-bold"><Trans id="terms.complete.title" comment="Heading identifying the full English legal document below the translated summary">Complete Terms of Service (English)</Trans></h2>
            {children}
          </section>

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
              <Trans id="terms.fullTermsLink" comment="Link to full terms of service text">Terms of Service on GitHub</Trans>
              <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
            </a>
          </div>
        </div>
      </div>
    </main>
  );
}
