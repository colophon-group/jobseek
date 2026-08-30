/**
 * Public query vocabulary shared by the MCP server and external API clients.
 *
 * Keep this module dependency-free: consumers that only need to build URLs or
 * validate generated OpenAPI can import it without loading the MCP SDK.
 */
export const API_LOCALES = ["en", "de", "fr", "it"] as const;

export const DEFAULT_API_LOCALE = API_LOCALES[0];

export const PUBLIC_API_VERSION = "1.2.0";

export const PUBLIC_SEARCH_QUERY_PARAMETERS = [
  "q",
  "loc",
  "occ",
  "sen",
  "tech",
  "wm",
  "etype",
  "sal",
  "exp",
  "lang",
  "locale",
] as const;

export const SEARCH_WORK_MODE_VALUES = [
  "onsite",
  "hybrid",
  "remote",
] as const;

export const SEARCH_EMPLOYMENT_TYPE_VALUES = [
  "full_time",
  "part_time",
  "contract",
  "internship",
  "temporary",
  "volunteer",
] as const;

function commaSeparatedPattern(values: readonly string[]): string {
  const alternatives = values.join("|");
  return `^\\s*(?:${alternatives})(?:\\s*,\\s*(?:${alternatives}))*\\s*$`;
}

export const SEARCH_WORK_MODE_LIST_PATTERN = commaSeparatedPattern(
  SEARCH_WORK_MODE_VALUES,
);

export const SEARCH_EMPLOYMENT_TYPE_LIST_PATTERN = commaSeparatedPattern(
  SEARCH_EMPLOYMENT_TYPE_VALUES,
);

export const SEARCH_LANGUAGE_LIST_PATTERN = commaSeparatedPattern(API_LOCALES);

export const SEARCH_INTEGER_RANGE_PATTERN =
  "^\\s*(?:\\d+\\s*-\\s*\\d+|\\d+\\s*-\\s*|-\\s*\\d+)\\s*$";
