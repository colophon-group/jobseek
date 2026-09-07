import { createHash } from "node:crypto";
import { parseFragment, type DefaultTreeAdapterTypes } from "parse5";

export const CLASSIFIER_INPUT_SCHEMA_VERSION = "classifier-input-v1" as const;
export const CLASSIFIER_INPUT_NORMALIZER_VERSION =
  "classifier-input-normalizer-v1" as const;
export const CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT = 12_000;

export type ClassifierInputSource = {
  candidateId: string;
  title: string;
  companyName: string;
  descriptionHtml: string;
  selectedDescriptionLocale: string;
};

/** The complete allowlist serialized for the model. */
export type ClassifierInputV1 = {
  schemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  candidateId: string;
  title: string;
  companyName: string;
  descriptionText: string;
};

/** Evaluation/runtime metadata which must never be serialized into the model payload. */
export type ClassifierInputSidecarV1 = {
  selectedDescriptionLocale: string;
  truncated: boolean;
};

export type NormalizedClassifierInputV1 = {
  payload: ClassifierInputV1;
  sidecar: ClassifierInputSidecarV1;
  /** Fixture identity only. AF-10 binds this to policy before any cache use. */
  contentIdentity: string;
};

const SOURCE_FIELDS = [
  "candidateId",
  "title",
  "companyName",
  "descriptionHtml",
  "selectedDescriptionLocale",
] as const;
const SOURCE_FIELD_SET = new Set<string>(SOURCE_FIELDS);

const OMITTED_SUBTREES = new Set([
  "base",
  "canvas",
  "embed",
  "head",
  "iframe",
  "link",
  "meta",
  "noscript",
  "object",
  "script",
  "style",
  "svg",
  "template",
  "title",
]);

const BLOCK_ELEMENTS = new Set([
  "address",
  "article",
  "aside",
  "blockquote",
  "dd",
  "details",
  "dialog",
  "div",
  "dl",
  "dt",
  "fieldset",
  "figcaption",
  "figure",
  "footer",
  "form",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "header",
  "li",
  "main",
  "nav",
  "ol",
  "p",
  "pre",
  "section",
  "summary",
  "table",
  "tbody",
  "td",
  "tfoot",
  "th",
  "thead",
  "tr",
  "ul",
]);

export class ClassifierInputValidationError extends Error {
  readonly path: string;
  readonly rule: string;

  constructor(path: string, rule: string) {
    super(`${path}: ${rule}`);
    this.name = "ClassifierInputValidationError";
    this.path = path;
    this.rule = rule;
  }
}

function fail(path: string, rule: string): never {
  throw new ClassifierInputValidationError(path, rule);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readRequiredString(
  source: Record<string, unknown>,
  field: (typeof SOURCE_FIELDS)[number],
): string {
  const value = source[field];
  if (typeof value !== "string") fail(`$.${field}`, "must be a string");
  return value;
}

function normalizeInlineText(value: string): string {
  return value
    .replace(/\r\n?/gu, "\n")
    .replace(/\u00a0/gu, " ")
    .normalize("NFC")
    .replace(/[^\S\n]+/gu, " ")
    .replace(/ *\n+ */gu, " ")
    .trim();
}

function normalizedRequiredText(value: string, path: string): string {
  const normalized = normalizeInlineText(value);
  if (normalized.length === 0) fail(path, "must contain text");
  return normalized;
}

function isElement(node: DefaultTreeAdapterTypes.Node): node is DefaultTreeAdapterTypes.Element {
  return "tagName" in node;
}

function hasHiddenSemantics(element: DefaultTreeAdapterTypes.Element): boolean {
  return element.attrs.some(
    (attribute) =>
      attribute.name === "hidden" ||
      (attribute.name === "aria-hidden" && attribute.value.trim().toLowerCase() === "true"),
  );
}

function appendVisibleText(
  node: DefaultTreeAdapterTypes.Node,
  chunks: string[],
): void {
  if (node.nodeName === "#text") {
    chunks.push(
      (node as DefaultTreeAdapterTypes.TextNode).value
        .replace(/\r\n?/gu, "\n")
        .replace(/\u00a0/gu, " ")
        .replace(/\s+/gu, " "),
    );
    return;
  }

  if (!isElement(node)) {
    if ("childNodes" in node) {
      for (const child of node.childNodes) appendVisibleText(child, chunks);
    }
    return;
  }

  const tagName = node.tagName.toLowerCase();
  if (OMITTED_SUBTREES.has(tagName) || hasHiddenSemantics(node)) return;
  if (tagName === "br" || tagName === "hr") {
    chunks.push("\n");
    return;
  }

  const isBlock = BLOCK_ELEMENTS.has(tagName);
  if (isBlock) chunks.push("\n");
  for (const child of node.childNodes) appendVisibleText(child, chunks);
  if (isBlock) chunks.push("\n");
}

function normalizeDescriptionHtml(descriptionHtml: string): string {
  const document = parseFragment(descriptionHtml);
  const chunks: string[] = [];
  for (const child of document.childNodes) appendVisibleText(child, chunks);

  return chunks
    .join("")
    .replace(/\r\n?/gu, "\n")
    .replace(/\u00a0/gu, " ")
    .normalize("NFC")
    .replace(/[^\S\n]+/gu, " ")
    .replace(/ *\n+ */gu, "\n")
    .trim();
}

function truncateDescription(descriptionText: string): {
  descriptionText: string;
  truncated: boolean;
} {
  const codePoints = Array.from(descriptionText);
  if (codePoints.length <= CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT) {
    return { descriptionText, truncated: false };
  }

  const prefix = codePoints.slice(0, CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT);
  const lastBlockBoundary = prefix.lastIndexOf("\n");
  if (lastBlockBoundary > 0) {
    return {
      descriptionText: prefix.slice(0, lastBlockBoundary).join("").trimEnd(),
      truncated: true,
    };
  }

  const lastWhitespace = prefix.lastIndexOf(" ");
  if (lastWhitespace > 0) {
    return {
      descriptionText: prefix.slice(0, lastWhitespace).join("").trimEnd(),
      truncated: true,
    };
  }

  return { descriptionText: prefix.join(""), truncated: true };
}

function assertStrictSource(input: unknown): ClassifierInputSource {
  if (!isRecord(input)) fail("$", "must be an object");

  for (const key of Object.keys(input)) {
    if (!SOURCE_FIELD_SET.has(key)) fail("$", "contains a field that is not allowed");
  }
  for (const field of SOURCE_FIELDS) {
    if (!Object.hasOwn(input, field)) fail(`$.${field}`, "field is required");
  }

  return {
    candidateId: readRequiredString(input, "candidateId"),
    title: readRequiredString(input, "title"),
    companyName: readRequiredString(input, "companyName"),
    descriptionHtml: readRequiredString(input, "descriptionHtml"),
    selectedDescriptionLocale: readRequiredString(input, "selectedDescriptionLocale"),
  };
}

function contentIdentityFor(payload: ClassifierInputV1): string {
  const canonicalSemanticContent = JSON.stringify({
    normalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    title: payload.title,
    companyName: payload.companyName,
    descriptionText: payload.descriptionText,
  });
  return createHash("sha256").update(canonicalSemanticContent, "utf8").digest("hex");
}

/**
 * Purely validates and projects an already-loaded posting. It performs no
 * loading, authorization, policy decision, provider call, or cache binding.
 */
export function normalizeClassifierInputV1(input: unknown): NormalizedClassifierInputV1 {
  const source = assertStrictSource(input);
  const normalizedDescription = normalizeDescriptionHtml(source.descriptionHtml);
  if (normalizedDescription.length === 0) {
    fail("$.descriptionHtml", "must contain visible text");
  }
  const { descriptionText, truncated } = truncateDescription(normalizedDescription);

  const payload: ClassifierInputV1 = {
    schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    candidateId: normalizedRequiredText(source.candidateId, "$.candidateId"),
    title: normalizedRequiredText(source.title, "$.title"),
    companyName: normalizedRequiredText(source.companyName, "$.companyName"),
    descriptionText,
  };

  return {
    payload,
    sidecar: {
      selectedDescriptionLocale: normalizedRequiredText(
        source.selectedDescriptionLocale,
        "$.selectedDescriptionLocale",
      ),
      truncated,
    },
    contentIdentity: contentIdentityFor(payload),
  };
}
