import { Suspense } from "react";
import { isLocale, defaultLocale } from "@/lib/i18n";
import { SettingsLoader } from "./settings-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

/**
 * The Suspense fallback matches the previous client-side spinner so
 * the perceived loading state is unchanged on cold loads — only the
 * happy path is faster (no client `useEffect` waterfall, see
 * `settings-loader.tsx`). cacheComponents requires every dynamic
 * subtree to live inside a `<Suspense>` boundary; `SettingsLoader`
 * reads `headers()`/`cookies()` transitively via Better Auth so it
 * is the dynamic hole here.
 */
function SettingsFallback() {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
    </div>
  );
}

export default async function SettingsPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return (
    <Suspense fallback={<SettingsFallback />}>
      <SettingsLoader locale={locale} />
    </Suspense>
  );
}
