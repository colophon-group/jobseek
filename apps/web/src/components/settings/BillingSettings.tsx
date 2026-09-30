"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { ArrowRight, Check, SlidersHorizontal } from "lucide-react";
import { BillingPolicyLinks, FreeAccessNote, ProPitch } from "@/components/pro/ProPitch";
import { ProWaitlistForm } from "@/components/pro/ProWaitlistForm";
import { useSession } from "@/components/providers/SessionProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { createCheckoutSession, createPortalSession } from "@/lib/actions/billing";
import { translateActionError } from "@/lib/action-error-messages";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import type { PlanId } from "@/lib/plans";
import {
  normalizeAuthReturnPath,
  withAuthReturnPath,
} from "@/lib/auth-return";

type PlanInfo = {
  plan: PlanId;
  checkoutEnabled?: boolean;
  hasBillingAccount?: boolean;
  trialEligible?: boolean;
  status?: string | null;
  periodEnd?: string | null;
  cancellationScheduled?: boolean;
};

export function BillingSettings({ planInfo }: { planInfo: PlanInfo }) {
  const { t, i18n } = useLingui();
  const lp = useLocalePath();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isLoggedIn } = useSession();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState<"portal" | "checkout" | null>(null);
  const returnPath = normalizeAuthReturnPath(searchParams.get("next"));
  const checkoutComplete = searchParams.get("checkout") === "complete";

  useEffect(() => {
    if (!checkoutComplete || planInfo.plan === "unlimited") return;
    let attempts = 0;
    const interval = window.setInterval(() => {
      router.refresh();
      if (++attempts >= 15) window.clearInterval(interval);
    }, 2000);
    return () => window.clearInterval(interval);
  }, [checkoutComplete, planInfo.plan, router]);

  useEffect(() => {
    if (isLoggedIn && planInfo.plan === "unlimited" && returnPath) {
      router.replace(returnPath);
    }
  }, [isLoggedIn, planInfo.plan, returnPath, router]);

  const isFree = planInfo.plan === "free";

  const hasAccess = !isFree;
  const canPurchase = isFree && (!planInfo.status || ["canceled", "incomplete_expired"].includes(planInfo.status ?? ""));
  const trialEligible = planInfo.trialEligible !== false;
  const needsPayment = ["past_due", "unpaid", "incomplete"].includes(planInfo.status ?? "");
  const isPaused = planInfo.status === "paused";
  const ended = ["canceled", "incomplete_expired"].includes(planInfo.status ?? "");
  const periodEnd = planInfo.periodEnd
    ? new Intl.DateTimeFormat(i18n.locale, { dateStyle: "long" }).format(new Date(planInfo.periodEnd))
    : null;
  const billingReturnPath = withAuthReturnPath(lp("/settings/billing"), returnPath);
  const loginPath = withAuthReturnPath(lp("/sign-in"), billingReturnPath);

  async function handleCheckout() {
    setError("");
    setLoading("checkout");
    try {
      const result = await createCheckoutSession(i18n.locale, returnPath);
      if (result.error) {
        setError(translateActionError(t, result.error));
        return;
      }
      if (!result.url) throw new Error("Missing checkout URL");
      window.location.href = result.url;
    } catch {
      setError(translateActionError(t, "payments_unavailable"));
    } finally {
      setLoading(null);
    }
  }

  async function handleManage() {
    setError("");
    setLoading("portal");
    try {
      const result = await createPortalSession(i18n.locale);
      if (result.error) setError(translateActionError(t, result.error));
      else if (result.url) window.location.href = result.url;
    } catch {
      setError(translateActionError(t, "billing_portal_unavailable"));
    } finally {
      setLoading(null);
    }
  }

  const manageButton = (
    <Button variant="outline" size="sm" onClick={handleManage} disabled={loading !== null}>
      {loading === "portal"
        ? t({ id: "settings.billing.managing", comment: "Manage subscription button loading state", message: "Loading…" })
        : t({ id: "settings.billing.manage", comment: "Manage subscription button label", message: "Manage subscription" })}
    </Button>
  );

  return (
    <div className="space-y-8 pb-8">
      {error && <ErrorAlert message={error} focusOnRender />}
      {checkoutComplete && !hasAccess && (
        <div className="rounded-xl border border-border-soft bg-surface p-5" role="status">
          <p className="font-semibold"><Trans id="pro.billing.confirming" comment="Heading after checkout while the webhook is pending">Confirming your subscription</Trans></p>
          <p className="mt-2 text-sm text-muted"><Trans id="settings.billing.processing" comment="Shown after checkout while waiting for verified subscription activation">We’re confirming your subscription. If your plan hasn’t updated yet, refresh shortly.</Trans></p>
        </div>
      )}

      {hasAccess || needsPayment || isPaused ? (
        <section className="overflow-hidden rounded-2xl border border-border-soft">
          <div className="border-b border-divider bg-surface p-6 sm:p-8">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="inline-flex items-center gap-2 text-sm"><SlidersHorizontal size={16} aria-hidden="true" /> Job Seek Pro</p>
              <span className="rounded-full border border-border-soft px-3 py-1 text-xs">
                {needsPayment ? t({ id: "pro.status.payment", comment: "Past-due subscription status", message: "Payment needs attention" })
                  : isPaused ? t({ id: "pro.status.paused", comment: "Paused subscription status", message: "Paused" })
                  : planInfo.cancellationScheduled ? t({ id: "pro.status.ending", comment: "Scheduled cancellation status", message: "Ending soon" })
                  : planInfo.status === "trialing" ? t({ id: "pro.status.trial", comment: "Trial subscription status", message: "Free trial" })
                  : t({ id: "pro.status.active", comment: "Active subscription status", message: "Active" })}
              </span>
            </div>
            <h2 className="mt-7 text-2xl font-semibold tracking-tight">
              {hasAccess
                ? t({ id: "pro.billing.ready", comment: "Headline for a subscribed user", message: "Your watchlists, with a little more focus." })
                : t({ id: "pro.billing.interrupted", comment: "Headline when Narrowed access is paused or past due", message: "Let’s get your filtering back." })}
            </h2>
            <p className="mt-3 text-sm leading-6 text-muted">
              {needsPayment || isPaused
                ? t({ id: "pro.billing.recover", comment: "Explains how to restore an interrupted subscription", message: "Open subscription management to review your billing and restore Narrowed results. Your free features are still available." })
                : t({ id: "pro.billing.use", comment: "Explains where subscribed users can use Pro", message: "Open a watchlist and choose Narrow results. Add your criteria, then browse the matches." })}
            </p>
            {hasAccess && <Button href={lp("/watchlists")} className="mt-5 gap-2">
              {t({ id: "pro.billing.watchlists", comment: "Subscriber CTA to use the filtering feature", message: "Go to your watchlists" })}<ArrowRight size={15} aria-hidden="true" />
            </Button>}
          </div>
          <div className="flex flex-col items-start justify-between gap-4 p-6 sm:flex-row sm:items-center sm:px-8">
            <div className="text-sm leading-6" role="status">
              {periodEnd ? (
                <>
                  <p className="text-muted">{planInfo.cancellationScheduled
                    ? t({ id: "pro.billing.accessUntil", comment: "Label for last day of Pro access after cancellation", message: "Pro access until" })
                    : planInfo.status === "trialing"
                      ? t({ id: "pro.billing.trialUntil", comment: "Label for the trial end date", message: "Your free trial ends" })
                      : t({ id: "pro.billing.renews", comment: "Label for the next billing period", message: "Next renewal" })}</p>
                  <p className="font-medium">{periodEnd}</p>
                  {planInfo.status === "trialing" && !planInfo.cancellationScheduled && <p className="mt-1 text-xs text-muted"><Trans id="pro.billing.trialRenewal" comment="Renewal disclosure during an active trial">Then US$10/month, with applicable taxes shown in your billing portal.</Trans></p>}
                </>
              ) : <p className="text-muted"><Trans id="pro.billing.manageHelp" comment="What users can do in the portal">Payment details, invoices, and cancellation</Trans></p>}
            </div>
            {planInfo.hasBillingAccount && manageButton}
          </div>
        </section>
      ) : checkoutComplete ? null : (
        <>
          {ended && <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border-soft p-5">
            <div>
              <h2 className="text-sm font-semibold"><Trans id="pro.billing.ended" comment="Subscription has ended and access is now free">Your Pro subscription has ended.</Trans></h2>
              <p className="mt-1 text-xs text-muted"><Trans id="pro.billing.saved" comment="Reassures users that cancellation keeps their free data and features">Your watchlists, saved jobs, and email alerts are still here.</Trans></p>
            </div>
            {planInfo.hasBillingAccount && manageButton}
          </div>}
          <ProPitch />
          <section id="pro-offer" className="scroll-mt-36 rounded-2xl border border-border-soft bg-surface p-6 sm:p-7">
            <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-center">
              <div>
                <h3 className="text-2xl font-semibold">{trialEligible
                  ? t({ id: "pro.offer.trial", comment: "Trial offer headline", message: "7 days free" })
                  : t({ id: "pro.offer.monthly", comment: "Returning subscriber price with explicit currency", message: "US$10 / month" })}</h3>
                <p className="mt-2 text-sm text-muted">{trialEligible
                  ? t({ id: "pro.offer.afterTrial", comment: "Price immediately below trial offer", message: "Then US$10 per month. Cancel anytime." })
                  : t({ id: "pro.offer.returning", comment: "Returning customers do not receive another trial", message: "Pick up where you left off. Billed monthly." })}</p>
              </div>
              {canPurchase && planInfo.checkoutEnabled && !checkoutComplete && (
                isLoggedIn ? <Button onClick={handleCheckout} disabled={loading !== null} className="gap-2 self-start sm:self-auto">
                  {loading === "checkout"
                    ? t({ id: "pro.offer.opening", comment: "Loading state while preparing secure checkout", message: "Opening checkout…" })
                    : trialEligible
                      ? t({ id: "settings.billing.startTrial", comment: "Button opening Stripe checkout for a seven-day trial", message: "Start 7-day free trial" })
                      : t({ id: "settings.billing.subscribe", comment: "Subscribe again without another trial", message: "Subscribe to Pro" })}
                  <ArrowRight size={16} aria-hidden="true" />
                </Button> : <Button href={loginPath} className="gap-2 self-start sm:self-auto">
                  {t({ id: "settings.billing.startTrial", comment: "Button opening Stripe checkout for a seven-day trial", message: "Start 7-day free trial" })}<ArrowRight size={16} aria-hidden="true" />
                </Button>
              )}
              {!planInfo.checkoutEnabled && <span className="text-sm text-muted"><Trans id="pro.offer.unavailable" comment="Honest availability notice when checkout is disabled">Trial signup isn’t open yet.</Trans></span>}
            </div>
            {!planInfo.checkoutEnabled && <div className="mt-5 border-t border-divider pt-4"><ProWaitlistForm /></div>}
            {planInfo.checkoutEnabled && <div className="mt-5 space-y-2 border-t border-divider pt-4 text-xs leading-5 text-muted">
              {trialEligible ? <p><Trans id="pro.offer.paymentTerms" comment="Payment method and cancellation disclosure next to trial CTA">Payment method required. Cancel before your trial ends to avoid being charged.</Trans></p>
                : <p><Trans id="pro.offer.repeatTerms" comment="Renewal disclosure for returning customers">Renews monthly until canceled. A new free trial is not included.</Trans></p>}
              <p><Trans id="pro.offer.checkout" comment="Payment processor and final price disclosure">Secure checkout with Stripe. Your final total, including applicable taxes, is shown before you confirm.</Trans></p>
              {!isLoggedIn && planInfo.checkoutEnabled && <p><Trans id="pro.offer.signIn" comment="Explains that anonymous visitors sign in before checkout">You’ll sign in first, then continue to checkout.</Trans></p>}
              <BillingPolicyLinks />
            </div>}
          </section>
        </>
      )}
      {planInfo.hasBillingAccount && !hasAccess && !needsPayment && !isPaused && !ended && manageButton}
      <FreeAccessNote />
      {canPurchase && <a className="inline-flex items-center gap-2 text-xs text-muted underline underline-offset-4 hover:text-foreground" href={returnPath ?? lp("/explore")}>
        <Check size={13} aria-hidden="true" /><Trans id="pro.offer.stayFree" comment="Low-pressure alternative to purchasing Pro">Keep searching for free</Trans>
      </a>}
    </div>
  );
}
