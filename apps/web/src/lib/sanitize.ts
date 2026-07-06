/**
 * Client-side HTML sanitization for job descriptions.
 *
 * Defense-in-depth: the crawler already strips dangerous tags and attributes
 * (see apps/crawler/src/shared/html_normalize.py), but we re-sanitize on the
 * read path to guard against R2 tampering or crawler bypass.
 */
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

const ALLOWED_TAG_SET = new Set(ALLOWED_TAGS);

// Drop both the tag and its contents for elements that can execute or embed
// active content. Other unknown tags are unwrapped so their text survives.
const DROP_CONTENT_TAGS = new Set([
  "script",
  "style",
  "iframe",
  "object",
  "embed",
  "template",
]);

const ELEMENT_NODE = 1;
const COMMENT_NODE = 8;

export function sanitizeJobHtml(html: string): string {
  if (html === "") return "";
  if (typeof document === "undefined") {
    // This module is imported by client components that Next may evaluate on
    // the server. If it is ever called there, fail closed rather than shipping
    // unsanitized HTML.
    return "";
  }

  const template = document.createElement("template");
  template.innerHTML = html;
  sanitizeChildren(template.content);
  return template.innerHTML;
}

function sanitizeChildren(parent: ParentNode): void {
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === COMMENT_NODE) {
      child.remove();
      continue;
    }

    if (child.nodeType !== ELEMENT_NODE) continue;

    const element = child as Element;
    const tagName = element.tagName.toLowerCase();

    if (DROP_CONTENT_TAGS.has(tagName)) {
      element.remove();
      continue;
    }

    sanitizeChildren(element);

    if (!ALLOWED_TAG_SET.has(tagName)) {
      element.replaceWith(...Array.from(element.childNodes));
      continue;
    }

    for (const attribute of Array.from(element.attributes)) {
      element.removeAttribute(attribute.name);
    }
  }
}
