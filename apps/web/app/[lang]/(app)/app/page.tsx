import { Trans } from "@lingui/react/macro";
import { Construction } from "lucide-react";
import { initI18nForPage } from "@/lib/i18n";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AppPage({ params }: Props) {
  await initI18nForPage(params);

  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <Construction className="mb-6 h-16 w-16 text-muted-foreground" />
      <h1 className="text-3xl font-bold">
        <Trans id="app.home.title" comment="Main app page heading">
          Under Active Development
        </Trans>
      </h1>
      <p className="mt-4 max-w-md text-muted-foreground">
        <Trans id="app.home.subtitle" comment="Main app page subtitle explaining the app is being built">
          Job Seek is being actively built. Stay tuned for updates!
        </Trans>
      </p>
      <a
        href="https://www.linkedin.com/company/jobseek-the-aggregator/"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-6 inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        <Trans id="app.home.followLinkedIn" comment="Link to follow Job Seek on LinkedIn">
          Follow us on LinkedIn
        </Trans>
      </a>
    </div>
  );
}
