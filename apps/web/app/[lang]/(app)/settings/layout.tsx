import type { ReactNode } from "react";
import { Trans } from "@lingui/react/macro";
import { initI18nForPage } from "@/lib/i18n";
import { SettingsNav } from "@/components/settings/SettingsNav";

type Props = {
  params: Promise<{ lang: string }>;
  children: ReactNode;
};

export default async function SettingsLayout({ params, children }: Props) {
  await initI18nForPage(params);

  return (
    <div className="mx-auto grid max-w-5xl gap-7 pb-12 pt-5 md:grid-cols-[180px_minmax(0,1fr)] md:gap-10 md:pt-10">
      <aside>
        <h1 className="mb-4 text-2xl font-semibold md:mb-7">
          <Trans id="settings.title" comment="Settings page heading">
            Settings
          </Trans>
        </h1>
        <SettingsNav />
      </aside>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
