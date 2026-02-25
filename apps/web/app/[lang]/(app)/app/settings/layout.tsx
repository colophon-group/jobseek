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
    <>
      <div className="fixed left-0 right-0 top-0 z-40 border-b border-divider bg-surface-alpha backdrop-blur-md md:top-12">
        <div className="mx-auto max-w-[1200px] px-4">
          <div className="mx-auto max-w-4xl">
            <h1 className="pt-4 pb-2 text-2xl font-bold">
              <Trans id="settings.title" comment="Settings page heading">Settings</Trans>
            </h1>
            <SettingsNav />
          </div>
        </div>
      </div>
      <div className="mx-auto max-w-4xl pt-20">{children}</div>
    </>
  );
}
