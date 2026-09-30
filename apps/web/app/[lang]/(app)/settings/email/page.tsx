import { Suspense } from "react";
import { initI18nForPage } from "@/lib/i18n";
import { EmailSettingsLoader } from "./email-settings-loader";

export default async function EmailSettingsPage({
  params,
}: {
  params: Promise<{ lang: string }>;
}) {
  // Layouts are reused during client navigation; this RSC request needs its own catalog.
  const locale = await initI18nForPage(params);
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
      <EmailSettingsLoader locale={locale} />
    </Suspense>
  );
}
