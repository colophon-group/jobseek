"use client";

import { useEffect, useState } from "react";
import { getPlanInfo } from "@/lib/actions/billing";
import { BillingSettings } from "@/components/settings/BillingSettings";

type PlanInfo = Awaited<ReturnType<typeof getPlanInfo>>;

export function BillingLoader({ locale: _locale }: { locale: string }) {
  const [data, setData] = useState<PlanInfo | null>(null);

  useEffect(() => {
    getPlanInfo().then(setData);
  }, []);

  if (!data) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
      </div>
    );
  }

  return <BillingSettings planInfo={data} />;
}
