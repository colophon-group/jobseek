"use client";

import { useEffect, useState } from "react";
import { getAccountPageData } from "@/lib/actions/preferences";
import { AccountSettings } from "@/components/settings/AccountSettings";

export function AccountLoader({ locale: _locale }: { locale: string }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getAccountPageData>> | undefined>(undefined);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getAccountPageData().then((result) => {
      setData(result);
      setLoaded(true);
    });
  }, []);

  if (!loaded) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
      </div>
    );
  }

  return <AccountSettings initialData={data} />;
}
