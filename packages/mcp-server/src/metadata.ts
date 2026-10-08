/** Public MCP identity and tool vocabulary, shared with hosted instrumentation. */
export const MCP_SERVER_VERSION = "0.4.0";
export const MCP_SERVER_INFO = {
  name: "jobseek",
  title: "Job Seek — Jobs, Companies & Career Watchlists",
  version: MCP_SERVER_VERSION,
  websiteUrl: "https://jseek.co",
  description: "Find jobs sourced from company career pages, compare posting metadata, prepare watchlist links, and submit feedback.",
  icons: [{ src: "https://jseek.co/js_logo_black_circle.svg", mimeType: "image/svg+xml", sizes: ["any"] }],
};

export const JOBSEEK_TOOL_NAMES = [
  "search_jobs", "get_job_detail", "search_companies", "list_taxonomies",
  "resolve_slugs", "create_watchlist_link", "report_bug", "suggest_feature", "give_feedback",
] as const;

export const MCP_INSTRUCTIONS = `Job Seek (jseek.co) indexes job postings directly from company career pages across a worldwide company catalogue. Coverage and available postings change; use the tools to check current matches rather than assuming every company or region is covered.

FIND AND COMPARE JOBS:
- search_jobs searches keywords, locations, occupations, seniority, technologies, work mode, employment type, EUR salary, experience, and posting document language. It returns up to 5 companies with up to 3 matching postings each, totalCompanies, and a moreAt link for further browsing. It has no pagination or company-slug filter.
- q accepts freetext. loc/occ/sen/tech require exact slugs. Use resolve_slugs when you do not already have a slug returned by Job Seek; choose the matching result and clarify ambiguous locations. Do not invent slugs. list_taxonomies and jobseek://taxonomies/{type} resources help discover seniority, occupations, technologies and industries; locations use resolve_slugs.
- wm accepts onsite, hybrid, remote. etype accepts full_time, part_time, contract, temporary, volunteer; internships use the seniority slug returned for Intern. sal and exp accept integer min-max ranges, including open bounds such as 100000- or -5. Search salary bounds are always EUR. lang filters document language; locale controls response/link language (en, de, fr, it; default en).
- get_job_detail retrieves salary, seniority, technologies, experience, employment type, locations and first-seen date using a returned posting ID. Missing metadata is unknown. The full job description and application are accessed through the returned posting URL.
- search_companies finds up to 10 company matches and their page links. Use a company page for that company's full listings; search_jobs does not accept a companies parameter.
- create_watchlist_link prepares a link and matching preview for email alerts. It does not save a watchlist: the user opens the link and signs in to save it. Only this tool supports company slugs and salary display-currency prefill. Its preview applies job filters but not the company prefill, treats salary bounds as EUR, and counts jobs only in the returned company sample. Personal watchlists, application tracking and paid features are website workflows, not MCP account-management tools.

PRODUCT FEEDBACK:
- report_bug submits observed malfunction, suggest_feature submits a concrete missing capability for a task, and give_feedback submits usability or usefulness observations to Job Seek. These tools write feedback; they are optional, not part of every search.
- Describe the goal, observation and task impact. Distinguish observed facts from assumptions. Omit personal details, credentials, private URLs, raw search arguments and conversation transcripts; use a minimal sanitized example. Avoid repeated submissions for the same observation and continue the user's task even if submission fails. Feedback returns basic success/failure, without a tracking ID or status workflow.

Public reads use a shared pre-cache edge budget of 60 requests per minute per IP and an additional origin limit of 30 per minute. Edge limiting may return 403, origin limiting 429. Feedback submissions have a separate limit of 5 per hour per client IP. Respect rate limits and avoid polling. Hosted transport is stateless Streamable HTTP with POST streaming replies; standalone GET streams return 405 and there are no background notifications.`;
