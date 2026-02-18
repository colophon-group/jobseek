import { Trans } from "@lingui/react/macro";
import { initI18nForPage } from "@/lib/i18n";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function HomePage({ params }: Props) {
  await initI18nForPage(params);

  return (
    <main>
      <h1>
        <Trans id="home.hero.title" comment="Main heading on the landing page">Find your next opportunity</Trans>
      </h1>
      <p>
        <Trans id="home.hero.subtitle" comment="Subheading below the main title on the landing page">Welcome to Jobseek</Trans>
      </p>
    </main>
  );
}
