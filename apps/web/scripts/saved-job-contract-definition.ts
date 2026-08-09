export const SAVED_JOB_TEXT_CHECK_DEFINITION =
  "CHECK (NULLIF(btrim(posting_title), ''::text) IS NOT NULL AND NULLIF(btrim(posting_source_url), ''::text) IS NOT NULL AND NULLIF(btrim(company_name), ''::text) IS NOT NULL AND NULLIF(btrim(company_slug), ''::text) IS NOT NULL)";

export function isExactSavedJobTextCheck(definition: string): boolean {
  return definition === SAVED_JOB_TEXT_CHECK_DEFINITION;
}
