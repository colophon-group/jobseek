import { Suspense } from "react";
import { isLocale, defaultLocale } from "@/lib/i18n";
import { EmailSettingsLoader } from "./email-settings-loader";

export default async function EmailSettingsPage({
  params,
}: {
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  return (
    <Suspense
      fallback={
        <div className="space-y-4" aria-busy="true">
          <div className="h-7 w-24 animate-pulse rounded bg-border-soft" />
          <div className="h-20 animate-pulse rounded bg-border-soft" />
          <div className="h-20 animate-pulse rounded bg-border-soft" />
        </div>
      }
    >
      <EmailSettingsLoader locale={isLocale(lang) ? lang : defaultLocale} />
    </Suspense>
  );
}
