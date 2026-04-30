import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { defaultLocale, isLocale, loadCatalog } from "@/lib/i18n";
import { getSessionUserId } from "@/lib/sessionCache";
import { CompanyRequestPageForm } from "./company-request-page-form";

/**
 * `/[lang]/companies/request` — landing page for users who tried to find a
 * company that isn't in the catalog yet.
 *
 * - Reads `?name=` / `?website=` from the query string and prefills the form.
 * - Heading: "Sorry, *{name}* isn't in our catalog yet. Want us to add it?"
 *   When `?name` is empty: "Request a company".
 * - Auth-gated: signed-in users only. Unauthed visitors are redirected to
 *   `/[lang]/sign-in?next=/[lang]/companies/request?...` so they come back.
 * - On successful submit, the embedded form renders the AgentPromptCard
 *   inline — the page does NOT redirect.
 *
 * @see colophon-group/jobseek#2808 (this issue)
 * @see colophon-group/jobseek#2806 (agent-prompt card + RequestCompanySuccess)
 * @see colophon-group/jobseek#2807 (search-bar dropdown that links here)
 */
export const metadata: Metadata = {
  // The landing page is essentially a form — no value to crawlers.
  robots: { index: false, follow: false },
};

interface PageProps {
  params: Promise<{ lang: string }>;
  searchParams: Promise<{ name?: string; website?: string }>;
}

function buildSelfPath(
  lang: string,
  params: { name?: string; website?: string },
): string {
  const qs = new URLSearchParams();
  if (params.name) qs.set("name", params.name);
  if (params.website) qs.set("website", params.website);
  const tail = qs.toString();
  return `/${lang}/companies/request${tail.length > 0 ? `?${tail}` : ""}`;
}

export default async function CompaniesRequestPage({
  params,
  searchParams,
}: PageProps) {
  const { lang } = await params;
  const sp = await searchParams;
  const locale = isLocale(lang) ? lang : defaultLocale;

  const userId = await getSessionUserId();
  if (!userId) {
    const next = encodeURIComponent(buildSelfPath(lang, sp));
    redirect(`/${lang}/sign-in?next=${next}`);
  }

  const { i18n } = await loadCatalog(locale);

  const name = (sp.name ?? "").trim();
  const website = (sp.website ?? "").trim();

  const heading = name
    ? i18n._({
        id: "companies.request.heading.named",
        comment:
          "Companies-request page heading when a company name was supplied via query string",
        message: "Sorry, {name} isn't in our catalog yet. Want us to add it?",
        values: { name },
      })
    : i18n._({
        id: "companies.request.heading.generic",
        comment:
          "Companies-request page heading when no company name was supplied",
        message: "Request a company",
      });

  const body = i18n._({
    id: "companies.request.body",
    comment:
      "Companies-request page body paragraph explaining the agent-driven request flow",
    message:
      "Tell us the company name and (ideally) a careers-page URL. We'll prepare a prompt for your AI agent to add it to jobseek through Murmur — usually faster than waiting for our weekly batch.",
  });

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col items-start gap-4 py-8">
      <h1 className="text-2xl font-semibold text-foreground">{heading}</h1>
      <p className="text-sm text-muted">{body}</p>
      <CompanyRequestPageForm
        locale={locale}
        defaultName={name || undefined}
        defaultWebsite={website || undefined}
      />
    </div>
  );
}
