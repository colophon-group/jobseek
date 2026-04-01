import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { LlmContentMirror } from "@/components/LlmContentMirror";
import { FaqContent } from "./faq-content";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "faq.meta.title", message: "FAQ" });
  const description = i18n._({
    id: "faq.meta.description",
    message: "Answers to common questions about Job Seek — how we crawl career pages, what the free and Pro plans include, how we handle your data, and how to opt out.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/faq", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/faq` },
  };
}

export default async function FaqPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;

  const faqItems = [
    {
      q: i18n._({ id: "faq.q.whatIsJobseek", message: "What is Job Seek?" }),
      a: i18n._({ id: "faq.a.whatIsJobseek", message: "Job Seek is a job search engine that scrapes career pages directly from company websites. Roles appear here before they hit large aggregators like LinkedIn or Indeed." }),
    },
    {
      q: i18n._({ id: "faq.q.howOftenUpdated", message: "How often are job listings updated?" }),
      a: i18n._({ id: "faq.a.howOftenUpdated", message: "The crawler discovers new postings on an hourly cycle and refreshes job details daily. Most roles appear within hours of being published on a company's careers page." }),
    },
    {
      q: i18n._({ id: "faq.q.whichAts", message: "Which ATS platforms do you support?" }),
      a: i18n._({ id: "faq.a.whichAts", message: "We integrate with Workday, Greenhouse, Lever, Ashby, Rippling, SmartRecruiters, Workable, and over a dozen other platforms. We also parse sitemaps and JSON APIs from any careers page." }),
    },
    {
      q: i18n._({ id: "faq.q.requestCompany", message: "How do I request a company that isn't listed?" }),
      a: i18n._({ id: "faq.a.requestCompany", message: "Use the request form on the explore page — paste a careers page URL or company name and we'll start indexing it. You can track progress via the issue number we return." }),
    },
    {
      q: i18n._({ id: "faq.q.freeVsPro", message: "What's the difference between Free and Pro?" }),
      a: i18n._({ id: "faq.a.freeVsPro", message: "Free gives you full search across all companies, one watchlist, and the application tracker with interview logging. Pro adds unlimited watchlists and email alerts when new roles match your criteria." }),
    },
    {
      q: i18n._({ id: "faq.q.whatIsWatchlist", message: "What is a watchlist?" }),
      a: i18n._({ id: "faq.a.whatIsWatchlist", message: "A watchlist is a saved search with optional company filtering. Pick the companies you care about, set your filters (role, location, seniority, salary), and get a live feed of matching jobs. You can share watchlists publicly or keep them private." }),
    },
    {
      q: i18n._({ id: "faq.q.trackerLimit", message: "Is there a limit to how many jobs I can track?" }),
      a: i18n._({ id: "faq.a.trackerLimit", message: "No. The application tracker has no hard limit — save as many jobs as you want and move them through your pipeline." }),
    },
    {
      q: i18n._({ id: "faq.q.crawlingPolicy", message: "How does the crawler behave on my company's website?" }),
      a: i18n._({ id: "faq.a.crawlingPolicy", message: "We respect robots.txt and TDM-Reservation headers, limit requests to one per site per minute, and use exponential backoff. All requests identify themselves via User-Agent. See our Job Indexing page for full details." }),
    },
    {
      q: i18n._({ id: "faq.q.optOut", message: "Can I opt out of Jobseek indexing my company?" }),
      a: i18n._({ id: "faq.a.optOut", message: "Yes. Email us and we'll stop crawling your careers site immediately and remove your postings from the index." }),
    },
    {
      q: i18n._({ id: "faq.q.openSource", message: "Is the crawler open source?" }),
      a: i18n._({ id: "faq.a.openSource", message: "Yes. The crawler and extraction pipeline are fully open source on GitHub. The application code is MIT licensed; job data is CC BY-NC 4.0." }),
    },
    {
      q: i18n._({ id: "faq.q.dataPrivacy", message: "Does Jobseek sell my data?" }),
      a: i18n._({ id: "faq.a.dataPrivacy", message: "No. We don't sell, rent, or share your data for marketing. Cookies are session-only with no ads or tracking. You can delete your account and all data is wiped within 30 days." }),
    },
    {
      q: i18n._({ id: "faq.q.languages", message: "Which languages does Jobseek support?" }),
      a: i18n._({ id: "faq.a.languages", message: "The interface is available in English, German, French, and Italian. You can also filter job postings by the language they were written in." }),
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
      <LlmContentMirror locale={locale}>
        <h1>{i18n._("faq.title")}</h1>
        <p>Everything you need to know about Job Seek. Can&apos;t find what you&apos;re looking for? Email us at {siteConfig.indexing.contactEmail}.</p>
        {faqItems.map((item, idx) => (
          <div key={idx}>
            <h2>{item.q}</h2>
            <p>{item.a}</p>
          </div>
        ))}
      </LlmContentMirror>
    </>
  );
}
