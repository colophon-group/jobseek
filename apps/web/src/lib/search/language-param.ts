import { API_LOCALES } from "@jseek/mcp-server/public-api-contract";

export type PublicSearchLanguageResult =
  | { ok: true; languages: string[] | null }
  | { ok: false; error: string };

/** Parse the public `lang` query without consulting UI preference state. */
export function parsePublicSearchLanguages(
  raw: string | null | undefined,
): PublicSearchLanguageResult {
  if (raw == null) return { ok: true, languages: null };
  const parts = raw
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return {
      ok: false,
      error: `Invalid 'lang' param: must be a comma-separated list of language codes (${API_LOCALES.join(", ")})`,
    };
  }
  const invalid = parts.filter(
    (value) => !(API_LOCALES as readonly string[]).includes(value),
  );
  if (invalid.length > 0) {
    return {
      ok: false,
      error: `Invalid 'lang' value(s): ${invalid.join(", ")}. Supported: ${API_LOCALES.join(", ")}`,
    };
  }
  return { ok: true, languages: [...new Set(parts)] };
}

/** Parse Explore links, where `*` explicitly preserves REST's all-language default. */
export function parseExploreSearchLanguages(
  raw: string | null | undefined,
): PublicSearchLanguageResult {
  if (raw?.trim() === "*") return { ok: true, languages: [] };
  return parsePublicSearchLanguages(raw);
}
