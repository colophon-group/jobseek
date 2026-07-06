import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { FaqContent } from "./faq-content";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({
    id: "faq.meta.title",
    comment: "SEO title for the public FAQ page.",
    message: "FAQ",
  });
  const description = i18n._({
    id: "faq.meta.description",
    comment: "SEO description for the public FAQ page.",
    message: "Answers to common questions about Job Seek — how we crawl career pages, what the free and Pro plans include, how we handle your data, and how to opt out.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/faq", locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/faq`,
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Job Seek" }],
    },
  };
}

export default async function FaqPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;

  const faqItems = [
    {
      q: i18n._({ id: "faq.q.whatIsJobseek", comment: "FAQ question asking for a short product definition.", message: "What is Job Seek?" }),
      a: i18n._({ id: "faq.a.whatIsJobseek", comment: "FAQ answer explaining what Job Seek does for targeted job seekers.", message: "Job Seek helps you track the companies you actually want to work at. Build a watchlist, get email alerts when new roles open up, and track applications in one place. Postings come straight from company career pages, so you see them within hours of going live — typically before LinkedIn or Indeed cross-post them." }),
    },
    {
      q: i18n._({ id: "faq.q.targetedSeeker", comment: "FAQ question about whether the product suits company-targeted job seekers.", message: "Is Job Seek for me if I already know which companies I want to work for?" }),
      a: i18n._({ id: "faq.a.targetedSeeker", comment: "FAQ answer explaining the company watchlist use case.", message: "Yes — that's the use case we built around. Add the companies you care about to a watchlist, set your filters (role, location, seniority, salary), and Job Seek monitors their career pages and alerts you the moment new roles match. You don't have to keep checking each company's site by hand or fight LinkedIn's algorithm." }),
    },
    {
      q: i18n._({ id: "faq.q.howOftenUpdated", comment: "FAQ question about job listing refresh cadence.", message: "How often are job listings updated?" }),
      a: i18n._({ id: "faq.a.howOftenUpdated", comment: "FAQ answer explaining crawler discovery and refresh frequency.", message: "The crawler discovers new postings on an hourly cycle and refreshes job details daily. Most roles appear within hours of being published on a company's careers page." }),
    },
    {
      q: i18n._({ id: "faq.q.differentiation", comment: "FAQ question comparing Job Seek with general job boards.", message: "What makes Job Seek different from LinkedIn or Indeed?" }),
      a: i18n._({ id: "faq.a.differentiation", comment: "FAQ answer explaining direct career-page indexing and no recruiter spam.", message: "We index company career pages directly, not third-party feeds. No recruiter spam, no reposted ghost jobs, and we re-check companies frequently — so most roles show up here within hours of being published. We're built for users who already know which companies they want to work at, not broad 'find me any job' searches." }),
    },
    {
      q: i18n._({ id: "faq.q.requestCompany", comment: "FAQ question about requesting a missing company.", message: "How do I request a company that isn't listed?" }),
      a: i18n._({ id: "faq.a.requestCompany", comment: "FAQ answer explaining how to request a missing company from the Explore page.", message: "Use the request form on the explore page — paste a careers page URL or company name and we'll start indexing it. You can track progress via the issue number we return." }),
    },
    {
      q: i18n._({ id: "faq.q.freeVsPro", comment: "FAQ question comparing the Free and Pro plans.", message: "What's the difference between Free and Pro?" }),
      a: i18n._({ id: "faq.a.freeVsPro", comment: "FAQ answer summarizing Free and Pro plan differences.", message: "Free gives you full search across all companies, one watchlist, and the application tracker with interview logging. Pro adds unlimited watchlists and email alerts when new roles match your criteria." }),
    },
    {
      q: i18n._({ id: "faq.q.whatIsWatchlist", comment: "FAQ question defining a watchlist.", message: "What is a watchlist?" }),
      a: i18n._({ id: "faq.a.whatIsWatchlist", comment: "FAQ answer explaining saved-search watchlists and sharing options.", message: "A watchlist is a saved search with optional company filtering. Pick the companies you care about, set your filters (role, location, seniority, salary), and get a live feed of matching jobs. You can share watchlists publicly or keep them private." }),
    },
    {
      q: i18n._({ id: "faq.q.trackerLimit", comment: "FAQ question about application tracker limits.", message: "Is there a limit to how many jobs I can track?" }),
      a: i18n._({ id: "faq.a.trackerLimit", comment: "FAQ answer explaining that the application tracker has no hard limit.", message: "No. The application tracker has no hard limit — save as many jobs as you want and move them through your pipeline." }),
    },
    {
      q: i18n._({ id: "faq.q.crawlingPolicy", comment: "FAQ question for company operators about crawler behavior.", message: "How does the crawler behave on my company's website?" }),
      a: i18n._({ id: "faq.a.crawlingPolicy", comment: "FAQ answer summarizing crawler politeness, rate limits, and policy compliance.", message: "We respect robots.txt and TDM-Reservation headers, limit requests to one per site per minute, and use exponential backoff. All requests identify themselves via User-Agent. See our Job Indexing page for full details." }),
    },
    {
      q: i18n._({ id: "faq.q.optOut", comment: "FAQ question about company opt-out from indexing.", message: "Can I opt out of Jobseek indexing my company?" }),
      a: i18n._({ id: "faq.a.optOut", comment: "FAQ answer explaining how companies can opt out of indexing.", message: "Yes. Email us and we'll stop crawling your careers site immediately and remove your postings from the index." }),
    },
    {
      q: i18n._({ id: "faq.q.openSource", comment: "FAQ question about open-source availability.", message: "Is the crawler open source?" }),
      a: i18n._({ id: "faq.a.openSource", comment: "FAQ answer explaining crawler source availability and data licensing.", message: "Yes. The crawler and extraction pipeline are fully open source on GitHub. The application code is MIT licensed; job data is CC BY-NC 4.0." }),
    },
    {
      q: i18n._({ id: "faq.q.dataPrivacy", comment: "FAQ question about whether user data is sold.", message: "Does Jobseek sell my data?" }),
      a: i18n._({ id: "faq.a.dataPrivacy", comment: "FAQ answer explaining data privacy, cookies, and account deletion.", message: "No. We don't sell, rent, or share your data for marketing. Cookies are session-only with no ads or tracking. You can delete your account and all data is wiped within 30 days." }),
    },
    {
      q: i18n._({ id: "faq.q.languages", comment: "FAQ question about supported interface and job-posting languages.", message: "Which languages does Jobseek support?" }),
      a: i18n._({ id: "faq.a.languages", comment: "FAQ answer listing interface languages and posting-language filtering.", message: "The interface is available in English, German, French, and Italian. You can also filter job postings by the language they were written in." }),
    },
  ];

  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "FAQPage",
        mainEntity: faqItems.map((item) => ({
          "@type": "Question",
          name: item.q,
          acceptedAnswer: {
            "@type": "Answer",
            text: item.a,
          },
        })),
        url: `${siteConfig.url}/${locale}/faq`,
        inLanguage: locale,
      }} />
      <FaqContent items={faqItems} />
    </>
  );
}
