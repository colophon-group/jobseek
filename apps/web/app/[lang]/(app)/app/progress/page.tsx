import { Trans } from "@lingui/react/macro";
import { Construction, Building2, Briefcase } from "lucide-react";
import { siteConfig } from "@/content/config";
import { initI18nForPage } from "@/lib/i18n";
import { getStats } from "@/lib/actions/stats";
import { CompanyRequestForm } from "./company-request-form";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AppPage({ params }: Props) {
  const { lang } = await params;
  await initI18nForPage(params);
  const { companyCount, jobPostingCount } = await getStats();

  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <Construction className="mb-6 h-16 w-16 text-muted" />
      <h1 className="text-3xl font-bold">
        <Trans id="app.home.title" comment="Main app page heading">
          Under Active Development
        </Trans>
      </h1>
      <p className="mt-4 max-w-md text-muted">
        <Trans id="app.home.subtitle" comment="Main app page subtitle explaining the app is being built">
          Job Seek is being actively built. Stay tuned for updates!
        </Trans>
      </p>

      <div className="mt-10 flex gap-6">
        <div className="flex flex-col items-center rounded-md border border-divider bg-surface px-8 py-6">
          <Building2 className="mb-2 h-6 w-6 text-muted" />
          <span className="text-3xl font-bold">{companyCount.toLocaleString()}</span>
          <span className="mt-1 text-sm text-muted">
            <Trans id="app.home.stats.companies" comment="Label for the company counter on the app home page">
              Companies Tracked
            </Trans>
          </span>
        </div>
        <div className="flex flex-col items-center rounded-md border border-divider bg-surface px-8 py-6">
          <Briefcase className="mb-2 h-6 w-6 text-muted" />
          <span className="text-3xl font-bold">{jobPostingCount.toLocaleString()}</span>
          <span className="mt-1 text-sm text-muted">
            <Trans id="app.home.stats.jobPostings" comment="Label for the job postings counter on the app home page">
              Job Postings
            </Trans>
          </span>
        </div>
      </div>

      <div className="mt-10">
        <h2 className="text-xl font-semibold">
          <Trans id="app.home.request.heading" comment="Heading above the company request input on the app home page">
            Which company should we track?
          </Trans>
        </h2>
        <p className="mt-2 max-w-md text-sm text-muted">
          <Trans id="app.home.request.description" comment="Description below the heading explaining users can request a company">
            Paste a careers page URL for best results, or just type a company name.
          </Trans>
        </p>

        <CompanyRequestForm locale={lang} />
      </div>

      <a
        href={siteConfig.social.linkedin.href}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-10 inline-flex items-center justify-center whitespace-nowrap rounded-full font-semibold border border-primary bg-primary text-primary-contrast transition-opacity hover:opacity-90 px-5 py-2"
      >
        <Trans id="app.home.followLinkedIn" comment="Link to follow Job Seek on LinkedIn">
          Follow us on LinkedIn
        </Trans>
      </a>
    </div>
  );
}
