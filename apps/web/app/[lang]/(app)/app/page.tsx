import { Trans } from "@lingui/react/macro";
import { initI18nForPage } from "@/lib/i18n";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AppPage({ params }: Props) {
  await initI18nForPage(params);

  return (
    <div>
      <h1 className="text-2xl font-bold">
        <Trans id="app.home.title" comment="Main app page heading">Welcome to Job Seek</Trans>
      </h1>
      <p className="mt-2 text-muted">
        <Trans id="app.home.subtitle" comment="Main app page subtitle">Your job market dashboard.</Trans>
      </p>
    </div>
  );
}
