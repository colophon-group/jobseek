/**
 * Client-side HTML sanitization for job descriptions.
 *
 * Defense-in-depth: the crawler already strips dangerous tags and attributes
 * (see apps/crawler/src/shared/html_normalize.py), but we re-sanitize on the
 * read path to guard against R2 tampering or crawler bypass.
 */
import DOMPurify from "dompurify";

/** Must match _ALLOWED_TAGS in apps/crawler/src/shared/html_normalize.py */
const ALLOWED_TAGS = [
  "a",
  "b",
  "blockquote",
  "br",
  "code",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "i",
  "li",
  "ol",
  "p",
  "pre",
  "s",
  "strong",
  "u",
  "ul",
];

export function sanitizeJobHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: [],
  });
}
