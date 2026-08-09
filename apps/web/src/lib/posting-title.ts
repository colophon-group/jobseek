import { decodeHTML } from "entities";

/**
 * Decode character references that escaped upstream job titles.
 *
 * The decoded value is still rendered as plain React text, so markup-looking
 * source remains inert rather than becoming HTML.
 */
export function normalizePostingTitle(value: string | null | undefined): string | null {
  return value ? decodeHTML(value) : null;
}
