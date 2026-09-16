import { resolveJobLanguages } from "@/lib/job-languages";

/**
 * Resolve the language policy for a company page.
 *
 * General search defaults an empty stored preference to the UI language. A
 * company page is different: the company itself is already the primary scope,
 * and limiting the default view to the UI language can hide whole regional
 * career systems behind one canonical company identity. Keep explicit user
 * preferences, but make the company-page default all languages.
 */
export function resolveCompanyPageJobLanguages(
  storedJobLanguages: string[],
  locale: string,
): { jobLanguages: string[]; languages: string[] } {
  const jobLanguages =
    storedJobLanguages.length === 0 ? ["*"] : storedJobLanguages;

  return {
    jobLanguages,
    languages: resolveJobLanguages(jobLanguages, locale),
  };
}
