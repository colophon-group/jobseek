/**
 * Stable, locale-independent string collator for canonicalization keys
 * (cache keys, filter hashes, anything where the order must be the same
 * on every host regardless of the user's language).
 *
 * Why `Intl.Collator` and not `Array.prototype.sort`:
 *
 *   `["python", "übung", "zoom"].sort()`
 *     → `["python", "zoom", "übung"]`   // raw UTF-16 code unit order
 *
 *   `["python", "übung", "zoom"].sort(canonicalStringCompare)`
 *     → `["python", "übung", "zoom"]`   // accent-folded base order
 *
 * The first form produces different keys for `["übung","python"]` vs
 * `["python","übung"]` (after sorting) because U+00FC (`ü`) sorts after
 * `z` (U+007A) in raw UTF-16. That's a cache-key bug: two callers with
 * the same logical filter set hash to different keys.
 *
 * Locale choice: `"en"` is intentional and **stable across server,
 * client, and every user locale**. A canonicalization key MUST NOT
 * depend on the viewer's language — otherwise the same filter set
 * produces different keys for de-DE vs en-US viewers, again splitting
 * the cache. See issue #3221.
 *
 * `sensitivity: "base"` folds accents and case to their base form, so
 * `"Übung"`, `"übung"`, and `"UBUNG"` all compare equal in *order*
 * terms (ties resolve by string identity via the second comparator). We
 * leave `numeric: false` because filter slugs contain digits that
 * should sort lexicographically (`item10` before `item2` is fine for
 * keys — only display ordering wants `numeric: true`).
 */
const CANONICAL_COLLATOR = new Intl.Collator("en", {
  sensitivity: "base",
  numeric: false,
});

export const canonicalStringCompare = CANONICAL_COLLATOR.compare;

/**
 * Locale-aware comparator for **display** ordering — autocomplete
 * lists, dropdowns, anything a human reads. Pass the viewer's locale
 * so accented letters sort correctly in their language (e.g. `ö`
 * sorts with `o` in German, after `z` in Swedish).
 *
 * `sensitivity: "base"` matches the canonical collator so accented
 * variants sort next to their base letter. `numeric: true` means
 * `"Item 2"` comes before `"Item 10"` (natural sort), which is what
 * users expect for any list with numeric suffixes.
 */
export function makeDisplayStringCompare(locale: string): (a: string, b: string) => number {
  return new Intl.Collator(locale, { sensitivity: "base", numeric: true }).compare;
}
