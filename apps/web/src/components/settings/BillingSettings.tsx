"use client";

import { useState } from "react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { Check, Crown, AlertTriangle } from "lucide-react";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { useFollowedCompanies } from "@/components/FollowedCompaniesProvider";
import { createCheckoutSession, createPortalSession } from "@/lib/actions/billing";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import type { PlanId } from "@/lib/plans";

type PlanInfo = {
  plan: PlanId;
  maxFollowedCompanies: number;
  canReceiveAlerts: boolean;
};

function LoginPrompt() {
  const { t } = useLingui();
  const lp = useLocalePath();
  return (
    <div className="flex flex-col items-center gap-4 py-12 text-center">
      <p className="text-muted">
        <Trans id="settings.billing.loginRequired" comment="Message when user must log in to see billing settings">
          Please log in to manage your billing settings.
        </Trans>
      </p>
      <Button href={lp("/sign-in")} variant="primary" size="md">
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

function UsageSection({ planInfo }: { planInfo: PlanInfo }) {
  const { t } = useLingui();
  const { followCount } = useFollowedCompanies();
  const max = planInfo.maxFollowedCompanies;
  const isUnlimited = max === Number.MAX_SAFE_INTEGER;
  const pct = isUnlimited ? 0 : Math.min(100, Math.round((followCount / max) * 100));
  const atLimit = !isUnlimited && followCount >= max;

  return (
    <section>
      <h2 className="mb-1 text-lg font-semibold">
        <Trans id="settings.billing.usage.title" comment="Usage section heading in billing settings">
          Usage
        </Trans>
      </h2>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.billing.usage.description" comment="Usage section description">
          Your current resource usage on this plan.
        </Trans>
      </p>
      <div className="rounded-lg border border-border-soft p-4">
        <div className="mb-2 flex items-center justify-between text-sm">
          <span>
            <Trans id="settings.billing.usage.followedCompanies" comment="Followed companies usage label">
              Followed companies
            </Trans>
          </span>
          <span className={atLimit ? "font-semibold text-warning" : "text-muted"}>
            {followCount} / {isUnlimited
              ? t({ id: "settings.billing.usage.unlimited", comment: "Unlimited usage label", message: "Unlimited" })
              : max}
          </span>
        </div>
        {!isUnlimited && (
          <div className="h-2 overflow-hidden rounded-full bg-border-soft">
            <div
              className={`h-full rounded-full transition-all ${atLimit ? "bg-warning" : "bg-primary"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        {atLimit && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-warning">
            <AlertTriangle size={12} />
            <Trans id="settings.billing.usage.limitReached" comment="Warning when follow limit is reached">
              You&apos;ve reached your follow limit. Upgrade to follow more companies.
            </Trans>
          </p>
        )}
      </div>
    </section>
  );
}

export function BillingSettings({ planInfo }: { planInfo: PlanInfo }) {
  const { t } = useLingui();
  const { isLoggedIn } = useAuth();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState<"checkout" | "portal" | null>(null);

  if (!isLoggedIn) return <LoginPrompt />;

  const isFree = planInfo.plan === "free";

  const freePlanFeatures = [
    t({ id: "settings.billing.free.f1", comment: "Free plan feature: follow limit", message: "Follow up to 5 companies" }),
    t({ id: "settings.billing.free.f2", comment: "Free plan feature: search", message: "Full job search" }),
    t({ id: "settings.billing.free.f3", comment: "Free plan feature: save jobs", message: "Save jobs" }),
  ];

  const proPlanFeatures = [
    t({ id: "settings.billing.pro.f1", comment: "Pro plan feature: unlimited follows", message: "Unlimited company follows" }),
    t({ id: "settings.billing.pro.f2", comment: "Pro plan feature: alerts", message: "Email alerts for new postings" }),
    t({ id: "settings.billing.pro.f3", comment: "Pro plan feature: everything free", message: "Everything in Free" }),
  ];

  async function handleUpgrade() {
    setError("");
    setLoading("checkout");
    const result = await createCheckoutSession();
    setLoading(null);
    if (result.error) {
      setError(result.error);
      return;
    }
    if (result.url) {
      window.location.href = result.url;
    }
  }

  async function handleManage() {
    setError("");
    setLoading("portal");
    const result = await createPortalSession();
    setLoading(null);
    if (result.error) {
      setError(result.error);
      return;
    }
    if (result.url) {
      window.location.href = result.url;
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

        {error && <div className="mt-4"><ErrorAlert message={error} /></div>}

        <div className="mt-4">
          {isFree ? (
            <Button
              variant="primary"
              size="md"
              onClick={handleUpgrade}
              disabled={loading === "checkout"}
            >
              {loading === "checkout"
                ? t({ id: "settings.billing.upgrading", comment: "Upgrading button loading state", message: "Upgrading…" })
                : t({ id: "settings.billing.upgrade", comment: "Upgrade button label", message: "Upgrade to Pro" })}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="md"
              onClick={handleManage}
              disabled={loading === "portal"}
            >
              {loading === "portal"
                ? t({ id: "settings.billing.managing", comment: "Manage subscription button loading state", message: "Loading…" })
                : t({ id: "settings.billing.manage", comment: "Manage subscription button label", message: "Manage subscription" })}
            </Button>
          )}
        </div>
      </section>

      {/* Usage */}
      <UsageSection planInfo={planInfo} />
    </div>
  );
}
