import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { FaqContent } from "./faq-content";
import { MarketingLinks } from "@/components/marketing/MarketingPage";

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
    message: "How watchlists, weekly job alerts, application tracking, and Narrowed work. Answers about free features, Pro, sharing, and company career-page indexing.",
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
      images: [{ ...siteConfig.ogImage, alt: "Job Seek" }],
    },
  };
}

export default async function FaqPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;

  const questions = {
    whatIsJobseek: {
      q: i18n._({ id: "faq.q.whatIsJobseek", comment: "FAQ question asking for a short product definition.", message: "What is Job Seek?" }),
      a: i18n._({ id: "faq.a.whatIsJobseek", comment: "FAQ answer explaining what Job Seek does for targeted job seekers.", message: "Job Seek helps you follow employers, find jobs from company career pages, and track the roles you save on Job Seek. Watchlists combine your companies and search filters." }),
    },
    targetedSeeker: {
      q: i18n._({ id: "faq.q.targetedSeeker", comment: "FAQ question about whether the product suits company-targeted job seekers.", message: "Do I need to know which companies I want to follow?" }),
      a: i18n._({ id: "faq.a.targetedSeeker", comment: "FAQ answer explaining the company watchlist use case.", message: "No. Start with a role and location, then add companies as you discover them. Company filtering is optional: a watchlist can also be a saved search across employers." }),
    },
    howOftenUpdated: {
      q: i18n._({ id: "faq.q.howOftenUpdated", comment: "FAQ question about job listing refresh cadence.", message: "How often are job listings updated?" }),
      a: i18n._({ id: "faq.a.howOftenUpdated", comment: "FAQ answer explaining crawler discovery and refresh frequency.", message: "Career pages are checked regularly, but discovery and refresh times vary by source. Check the original posting before applying. Email digests run on a separate weekly schedule." }),
    },
    differentiation: {
      q: i18n._({ id: "faq.q.differentiation", comment: "FAQ question comparing Job Seek with general job boards.", message: "What makes Job Seek different from LinkedIn or Indeed?" }),
      a: i18n._({ id: "faq.a.differentiation", comment: "FAQ answer explaining direct career-page indexing and no recruiter spam.", message: "Job Seek combines direct career-page indexing, company-focused watchlists, and application tracking. Direct sourcing does not guarantee that a posting is newer than on another site or that the employer is still hiring." }),
    },
    requestCompany: {
      q: i18n._({ id: "faq.q.requestCompany", comment: "FAQ question about requesting a missing company.", message: "How do I request a company that isn't listed?" }),
      a: i18n._({ id: "faq.a.requestCompany", comment: "FAQ answer explaining how to request a missing company from the Explore page.", message: "Use the request form on Explore to submit a company name or careers page URL. A request starts a review; it does not guarantee indexing. When an issue number is available, use it to follow progress." }),
    },
    freeVsPro: {
      q: i18n._({ id: "faq.q.freeVsPro", comment: "FAQ question comparing the Free and Pro plans.", message: "What's the difference between Free and Pro?" }),
      a: i18n._({ id: "faq.a.freeVsPro", comment: "FAQ answer summarizing Free, Pro, and where to check trial availability.", message: "Free includes search, up to 10 watchlists, weekly email digests, and tracking for jobs saved on Job Seek. Pro adds Narrowed results. The subscription page shows current trial availability." }),
    },
    whatIsWatchlist: {
      q: i18n._({ id: "faq.q.whatIsWatchlist", comment: "FAQ question defining a watchlist.", message: "What is a watchlist?" }),
      a: i18n._({ id: "faq.a.whatIsWatchlist", comment: "FAQ answer explaining private-by-default saved-search watchlists and unlisted sharing.", message: "A watchlist is a saved search with optional company filtering. Pick the companies you care about, set your filters (role, location, seniority, salary), and get a live feed of matching jobs. Watchlists are private by default; when you choose to share an unlisted link, anyone with the link can view the results and clone the watchlist, while only you can manage the original." }),
    },
    trackerLimit: {
      q: i18n._({ id: "faq.q.trackerLimit", comment: "FAQ question about application tracker limits.", message: "Is there a limit to how many jobs I can track?" }),
      a: i18n._({ id: "faq.a.trackerLimit", comment: "FAQ answer explaining that the application tracker has no hard limit.", message: "No. The application tracker has no hard limit — save as many jobs as you want and move them through your pipeline." }),
    },
    emailTiming: {
      q: i18n._({ id: "faq.q.emailTiming", comment: "Customer question about emailTiming", message: "When will I receive email notifications?" }),
      a: i18n._({ id: "faq.a.emailTiming", comment: "Explains supported behavior for emailTiming", message: "Email notifications are opt-in weekly digests, not instant alerts. Verify your email and enable the watchlists you want included in Settings. New matches are combined into one digest, with up to 20 roles. You can pause notifications in Settings." }),
    },
    externalTracking: {
      q: i18n._({ id: "faq.q.externalTracking", comment: "Customer question about externalTracking", message: "Can I track a job from another website?" }),
      a: i18n._({ id: "faq.a.externalTracking", comment: "Explains supported behavior for externalTracking", message: "The tracker works with jobs indexed on Job Seek. It does not currently import applications from other websites or let you add an arbitrary external job." }),
    },
    remoteEligibility: {
      q: i18n._({ id: "faq.q.remoteEligibility", comment: "Customer question about remoteEligibility", message: "Does remote mean I can work from any country?" }),
      a: i18n._({ id: "faq.a.remoteEligibility", comment: "Explains supported behavior for remoteEligibility", message: "No. Remote jobs can restrict hiring countries, time zones, or work authorization. Posting language also does not establish the working language. Check the employer\u2019s stated requirements." }),
    },
    crawlingPolicy: {
      q: i18n._({ id: "faq.q.crawlingPolicy", comment: "FAQ question for company operators about crawler behavior.", message: "How does the crawler behave on my company's website?" }),
      a: i18n._({ id: "faq.a.crawlingPolicy", comment: "FAQ answer summarizing request identity, pacing, and the indexing-policy contact route.", message: "We pace requests per rate-limit domain and use exponential backoff. Most requests use a stable browser-compatible User-Agent, while source-specific paths may use an identifying Job Seek UA. See our Job Indexing page for current content-restriction handling and contact details." }),
    },
    optOut: {
      q: i18n._({ id: "faq.q.optOut", comment: "FAQ question about company opt-out from indexing.", message: "Can I opt out of Jobseek indexing my company?" }),
      a: i18n._({ id: "faq.a.optOut", comment: "FAQ answer explaining how companies can opt out of indexing.", message: "Yes. Email us and we'll stop crawling your careers site immediately and remove your postings from the index." }),
    },
    openSource: {
      q: i18n._({ id: "faq.q.openSource", comment: "FAQ question about open-source availability.", message: "Is the crawler open source?" }),
      a: i18n._({ id: "faq.a.openSource", comment: "FAQ answer explaining crawler source availability and data licensing.", message: "Yes. The crawler and extraction pipeline are fully open source on GitHub. The application code is MIT licensed; job data is CC BY-NC 4.0." }),
    },
    dataPrivacy: {
      q: i18n._({ id: "faq.q.dataPrivacy", comment: "FAQ question about whether user data is sold.", message: "Does Jobseek sell my data?" }),
      a: i18n._({ id: "faq.a.dataPrivacy", comment: "FAQ answer explaining data privacy, cookies, and account deletion.", message: "No. We do not sell, rent, or share your data for marketing. We use necessary sign-in cookies and preference storage, some of which persist between visits. Account deletion removes active account records; backups and provider records have separate retention periods explained in our privacy policy." }),
    },
    languages: {
      q: i18n._({ id: "faq.q.languages", comment: "FAQ question about supported interface and job-posting languages.", message: "Which languages does Jobseek support?" }),
      a: i18n._({ id: "faq.a.languages", comment: "FAQ answer listing interface languages and posting-language filtering.", message: "The interface is available in English, German, French, and Italian. You can also filter job postings by the language they were written in." }),
    },
    narrowed: {
      q: i18n._({ id: "faq.q.narrowed", comment: "FAQ question about narrowed", message: "What does Narrowed do?" }),
      a: i18n._({ id: "faq.a.narrowed", comment: "FAQ answer about narrowed", message: "Narrowed reads the job descriptions in your watchlist against a request you write, then brings the matching roles together. Use search filters to choose where to look and Narrowed for details about the work. It is included with Pro." }),
    },
    narrowedRequest: {
      q: i18n._({ id: "faq.q.narrowedRequest", comment: "FAQ question about narrowedRequest", message: "What should I write in Narrowed?" }),
      a: i18n._({ id: "faq.a.narrowedRequest", comment: "FAQ answer about narrowedRequest", message: "Describe the work you want and what you want to avoid. For example: “I want to work directly with users and turn their problems into product improvements. No people management.” This asks about responsibilities that a job title or keyword alone cannot establish." }),
    },
    narrowedEvidence: {
      q: i18n._({ id: "faq.q.narrowedEvidence", comment: "FAQ question about narrowedEvidence", message: "Can I rely on every Narrowed match?" }),
      a: i18n._({ id: "faq.a.narrowedEvidence", comment: "FAQ answer about narrowedEvidence", message: "Read the original description before applying. Narrowed can miss details or make mistakes, and a posting may leave important questions unanswered. If a role does not mention on-call, travel, or working hours, confirm those details with the employer." }),
    },
    apply: {
      q: i18n._({ id: "faq.q.apply", comment: "FAQ question about apply", message: "How do I apply and track my progress?" }),
      a: i18n._({ id: "faq.a.apply", comment: "FAQ answer about apply", message: "Open a job and follow the link to apply on the employer’s website. Save it on Job Seek, update its status as you apply or interview, and add interview dates and types. You control the tracker; Job Seek does not send applications or update their status automatically." }),
    },
  };

  const sections = [
    {
      id: "start",
      title: i18n._({ id: "faq.sections.start", comment: "FAQ topic heading and jump link", message: "Finding jobs" }),
      items: [questions.whatIsJobseek, questions.targetedSeeker, questions.remoteEligibility, questions.languages, questions.howOftenUpdated, questions.requestCompany],
    },
    {
      id: "watchlists",
      title: i18n._({ id: "faq.sections.watchlists", comment: "FAQ topic heading and jump link", message: "Watchlists and emails" }),
      items: [questions.whatIsWatchlist, questions.emailTiming],
    },
    {
      id: "narrowed",
      title: i18n._({ id: "faq.sections.narrowed", comment: "FAQ topic heading and jump link", message: "Narrowed and Pro" }),
      items: [questions.freeVsPro, questions.narrowed, questions.narrowedRequest, questions.narrowedEvidence],
    },
    {
      id: "tracking",
      title: i18n._({ id: "faq.sections.tracking", comment: "FAQ topic heading and jump link", message: "Applications and your account" }),
      items: [questions.apply, questions.trackerLimit, questions.externalTracking, questions.dataPrivacy],
    },
    {
      id: "indexing",
      title: i18n._({ id: "faq.sections.indexing", comment: "FAQ topic heading and jump link", message: "About Job Seek and indexing" }),
      items: [questions.differentiation, questions.crawlingPolicy, questions.optOut, questions.openSource],
    },
  ];
  const faqItems = sections.flatMap(section => section.items);

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
      <FaqContent sections={sections} />
      <div className="mx-auto max-w-[720px] px-4 pb-12"><MarketingLinks locale={locale} /></div>
    </>
  );
}
