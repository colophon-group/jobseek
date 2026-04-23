export const MAX_EXCLUDE_TITLES = 50;

/** Escape a string so it can be safely embedded in a RegExp pattern. */
export function escapeRegex(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Build a single case-insensitive regex matching any of the given keywords as
 * whole words. Uses lookahead/lookbehind instead of \b so that keywords
 * containing non-word characters (e.g. "c++", "sr.") also match correctly.
 *   - `senior` → matches "Senior Engineer" but not "Seniority"
 *   - `c++` → matches "C++ Developer"
 *   - `head of` → matches "Head of Product" but not "Heading of Design"
 */
export function buildExcludeTitleRegex(keywords: string[]): RegExp | null {
  if (keywords.length === 0) return null;
  const alternation = keywords.map(escapeRegex).join("|");
  return new RegExp(`(?<!\\w)(?:${alternation})(?!\\w)`, "i");
}

/**
 * Parse the URL `exclude=` param into a deduped, trimmed, capped array.
 * Case-insensitive dedupe keeps first occurrence.
 */
export function parseExcludeParam(raw: string | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of raw.split(",")) {
    const trimmed = token.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(trimmed);
    if (out.length >= MAX_EXCLUDE_TITLES) break;
  }
  return out;
}

/**
 * Serialize an array of keywords into a comma-separated URL param value, or
 * undefined when empty so callers can conditionally emit the param.
 */
export function serializeExcludeParam(keywords: string[]): string | undefined {
  if (keywords.length === 0) return undefined;
  return keywords.join(",");
}
