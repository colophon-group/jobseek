import type { ReactNode } from "react";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getPreferences } from "@/lib/actions/preferences";
import { AppHeader } from "@/components/AppHeader";
import { CookieBanner } from "@/components/CookieBanner";
import { PreferencesInitializer } from "@/components/PreferencesInitializer";

type Props = {
  params: Promise<{ lang: string }>;
  children: ReactNode;
};

export default async function AppLayout({ params, children }: Props) {
  const { lang } = await params;
  const session = await auth.api.getSession({ headers: await headers() });

  const prefs = session ? await getPreferences() : null;

  if (prefs?.locale && prefs.locale !== lang) {
    redirect(`/${prefs.locale}/app`);
  }

  return (
    <div className="flex min-h-dvh flex-col">
      {prefs && (
        <PreferencesInitializer
          theme={prefs.theme}
          cookieConsent={prefs.cookieConsent}
        />
      )}
      <AppHeader />
      <div className="flex min-h-0 flex-1 flex-col md:pt-12">
        <CookieBanner aboveBottomBar serverConsent={prefs?.cookieConsent} />
        <main className="mx-auto w-full max-w-[1200px] px-4 py-8 pb-20 md:pb-8">
          {children}
        </main>
      </div>
    </div>
  );
}
