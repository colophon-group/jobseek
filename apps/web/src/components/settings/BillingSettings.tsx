"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { Check, Crown } from "lucide-react";
import { useSession } from "@/components/providers/SessionProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { createCheckoutSession, createPortalSession } from "@/lib/actions/billing";
import { loadPaddle } from "@/lib/paddle/browser";
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

function LoginPrompt({ returnPath }: { returnPath: string | null }) {
  const { t } = useLingui();
  const lp = useLocalePath();
  return (
    <div className="flex flex-col items-center gap-4 py-12 text-center">
      <p className="text-muted">
        <Trans id="settings.billing.loginRequired" comment="Message when user must log in to see billing settings">
          Please log in to manage your billing settings.
        </Trans>
      </p>
      <Button
        href={withAuthReturnPath(lp("/sign-in"), returnPath)}
        variant="primary"
        size="md"
      >
        {t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })}
      </Button>
    </div>
  );
}

function PlanCard({
  name,
  price,
  features,
  isCurrent,
  highlighted,
}: {
  name: string;
  price: string;
  features: string[];
  isCurrent: boolean;
  highlighted?: boolean;
}) {
  const { t } = useLingui();
  return (
    <div
      className={`rounded-lg border p-5 ${
        highlighted
          ? "border-primary bg-primary/5"
          : "border-border-soft"
      } ${isCurrent ? "ring-2 ring-primary" : ""}`}
    >
      <div className="mb-3 flex items-center gap-2">
        {highlighted && <Crown size={16} className="text-primary" />}
        <h3 className="text-base font-semibold">{name}</h3>
        {isCurrent && (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {t({ id: "settings.billing.currentPlan", comment: "Badge on current plan card", message: "Current" })}
          </span>
        )}
      </div>
      <p className="mb-4 text-2xl font-bold">{price}</p>
      <ul className="space-y-2">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-2 text-sm text-muted">
            <Check size={14} className="mt-0.5 shrink-0 text-success" />
            {f}
          </li>
        ))}
      </ul>
    </div>
  );
}

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

  if (!isLoggedIn) return <LoginPrompt returnPath={returnPath} />;

  const isFree = planInfo.plan === "free";

  const freePlanFeatures = [
    t({ id: "settings.billing.free.f0", comment: "Free plan feature: universal watchlist limit", message: "Up to 10 watchlists" }),
    t({ id: "settings.billing.free.f1", comment: "Free plan feature: star companies", message: "Star companies" }),
    t({ id: "settings.billing.free.f2", comment: "Free plan feature: search", message: "Full job search" }),
    t({ id: "settings.billing.free.alerts", comment: "Email alerts available to everyone", message: "Email alerts for matching jobs" }),
    t({ id: "settings.billing.free.f3", comment: "Free plan feature: save jobs", message: "Save jobs" }),
  ];

  const proPlanFeatures = [
    t({ id: "settings.billing.pro.f2", comment: "Pro plan feature: everything free", message: "Everything in Free" }),
    t({ id: "settings.billing.pro.filtering", comment: "Pro unlocks AI filtering only", message: "AI filtering for your watchlists" }),
  ];

  async function handleCheckout() {
    setError("");
    setLoading("checkout");
    try {
      const result = await createCheckoutSession();
      if (result.error) {
        setError(translateActionError(t, result.error));
        return;
      }
      if (!result.transactionId) throw new Error("Missing checkout transaction");
      const paddle = await loadPaddle(i18n.locale);
      const destination = new URL(lp("/settings/billing"), window.location.origin);
      destination.searchParams.set("checkout", "complete");
      if (returnPath) destination.searchParams.set("next", returnPath);
      paddle.Checkout.open({
        transactionId: result.transactionId,
        ...(result.email ? { customer: { email: result.email } } : {}),
        settings: { locale: i18n.locale, successUrl: destination.href },
      });
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
      const result = await createPortalSession();
      if (result.error) setError(translateActionError(t, result.error));
      else if (result.url) window.location.href = result.url;
    } catch {
      setError(translateActionError(t, "billing_portal_unavailable"));
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="space-y-10">
      {/* Plan overview */}
      <section>
        <h2 className="mb-1 text-lg font-semibold">
          <Trans id="settings.billing.plan.title" comment="Plan section heading in billing settings">
            Plan
          </Trans>
        </h2>
        <p className="mb-4 text-sm text-muted">
          <Trans id="settings.billing.plan.description" comment="Plan section description">
            Choose the plan that works for you.
          </Trans>
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <PlanCard
            name={t({ id: "settings.billing.plan.free", comment: "Free plan name", message: "Free" })}
            price={t({ id: "settings.billing.plan.freePrice", comment: "Free plan price display", message: "$0 / month" })}
            features={freePlanFeatures}
            isCurrent={isFree}
          />
          <PlanCard
            name={t({ id: "settings.billing.plan.pro", comment: "Pro plan name", message: "Pro" })}
            price={t({ id: "settings.billing.plan.proPrice", comment: "Pro plan price display", message: "$10 / month" })}
            features={proPlanFeatures}
            isCurrent={!isFree}
            highlighted
          />
        </div>

        {error && <div className="mt-4"><ErrorAlert message={error} focusOnRender /></div>}

        {checkoutComplete && isFree && (
          <p className="mt-4 text-sm text-muted" role="status">
            <Trans id="settings.billing.processing" comment="Shown after checkout while waiting for verified subscription activation">
              We’re confirming your subscription. If your plan hasn’t updated yet, refresh shortly.
            </Trans>
          </p>
        )}

        {planInfo.status === "trialing" && planInfo.periodEnd && (
          <p className="mt-4 text-sm" role="status">
            <Trans id="settings.billing.trialEnds" comment="Trial end date in billing settings">
              Your free trial ends on {new Intl.DateTimeFormat(i18n.locale).format(new Date(planInfo.periodEnd))}.
            </Trans>
          </p>
        )}
        {planInfo.cancellationScheduled && (
          <p className="mt-4 text-sm text-muted">
            <Trans id="settings.billing.cancellationScheduled" comment="Subscription canceled at the end of the current billing period">
              Your subscription will end after the current period. You can manage it in the billing portal.
            </Trans>
          </p>
        )}

        {isFree && planInfo.checkoutEnabled && (!planInfo.status || planInfo.status === "canceled") && (
          <div className="mt-4 space-y-3">
            <Button onClick={handleCheckout} disabled={loading !== null}>
              {planInfo.trialEligible !== false
                ? t({ id: "settings.billing.startTrial", comment: "Button opening Paddle checkout for a seven-day trial", message: "Start 7-day free trial" })
                : t({ id: "settings.billing.subscribe", comment: "Subscribe again without another trial", message: "Subscribe to Pro" })}
            </Button>
            <p className="text-sm text-muted">
              {planInfo.trialEligible !== false && <Trans id="settings.billing.trialTerms" comment="Trial renewal disclosure next to checkout button">7 days free, then US$10 per month. Payment method required. Cancel before the trial ends to avoid being charged.</Trans>}
              {" "}<Trans id="settings.billing.paddleSeller" comment="Merchant of record and tax disclosure">Paddle handles payments and applicable taxes. Your final total is shown at checkout.</Trans>
            </p>
          </div>
        )}

        {(planInfo.hasBillingAccount || !isFree) && (
          <div className="mt-4">
            <Button
              variant="outline"
              size="md"
              onClick={handleManage}
              disabled={loading !== null}
            >
              {loading === "portal"
                ? t({ id: "settings.billing.managing", comment: "Manage subscription button loading state", message: "Loading…" })
                : t({ id: "settings.billing.manage", comment: "Manage subscription button label", message: "Manage subscription" })}
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}
