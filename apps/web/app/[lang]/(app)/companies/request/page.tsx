import type { Metadata } from "next";

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
  // The landing page is essentially a form, no value to crawlers.
  robots: { index: false, follow: false },
};

interface PageProps {
  params: Promise<{ lang: string }>;
  searchParams: Promise<{ name?: string; website?: string }>;
}

export default async function CompaniesRequestPage(_: PageProps) {
  throw new Error("not implemented");
}
