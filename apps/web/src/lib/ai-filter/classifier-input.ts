import { createHash } from "node:crypto";
import { parseFragment, type DefaultTreeAdapterTypes } from "parse5";

export const CLASSIFIER_INPUT_SCHEMA_VERSION = "classifier-input-v1" as const;
export const CLASSIFIER_INPUT_NORMALIZER_VERSION =
  "classifier-input-normalizer-v2" as const;
export const CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT = 12_000;
const CLASSIFIER_TRUNCATION_BOUNDARY_WINDOW = 1_000;

export type ClassifierInputSource = {
  readonly candidateId: string;
  readonly title: string;
  readonly companyName: string;
  readonly descriptionHtml: string;
  readonly selectedDescriptionLocale: string;
};

/** The complete allowlist serialized for the model. */
export type ClassifierInputV1 = {
  readonly schemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly candidateId: string;
  readonly title: string;
  readonly companyName: string;
  readonly descriptionText: string;
};

/** Evaluation/runtime metadata which must never be serialized into the model payload. */
export type ClassifierInputSidecarV1 = {
  readonly selectedDescriptionLocale: string;
  readonly truncated: boolean;
};

export type NormalizedClassifierInputV1 = {
  readonly payload: ClassifierInputV1;
  readonly sidecar: ClassifierInputSidecarV1;
  /** Fixture identity only. AF-10 binds this to policy before any cache use. */
  readonly contentIdentity: string;
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
  // A semantic boundary is useful only when it is close to the hard cap. An
  // early heading or space followed by one long block must not discard most
  // of the model-visible description.
  const earliestPreferredBoundary =
    CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT - CLASSIFIER_TRUNCATION_BOUNDARY_WINDOW;
  const lastBlockBoundary = prefix.lastIndexOf("\n");
  if (lastBlockBoundary >= earliestPreferredBoundary) {
    return {
      descriptionText: prefix.slice(0, lastBlockBoundary).join("").trimEnd(),
      truncated: true,
    };
  }

  const lastWhitespace = prefix.lastIndexOf(" ");
  if (lastWhitespace >= earliestPreferredBoundary) {
    return {
      descriptionText: prefix.slice(0, lastWhitespace).join("").trimEnd(),
      truncated: true,
    };
  }

  return { descriptionText: prefix.join(""), truncated: true };
}

function snapshotOwnDataProperties(input: unknown): Record<string, unknown> {
  if (typeof input !== "object" || input === null) {
    fail("$", "must be an object");
  }

  let descriptors: ReturnType<typeof Object.getOwnPropertyDescriptors>;
  try {
    descriptors = Object.getOwnPropertyDescriptors(input);
  } catch {
    fail("$", "own property descriptors could not be read");
  }

  const snapshot = Object.create(null) as Record<string, unknown>;
  for (const key of Reflect.ownKeys(descriptors)) {
    if (typeof key === "symbol") fail("$", "symbol fields are not allowed");

    const descriptor = descriptors[key];
    if (!("value" in descriptor)) fail("$", "accessor fields are not allowed");
    if (!descriptor.enumerable) fail("$", "non-enumerable fields are not allowed");
    snapshot[key] = descriptor.value;
  }

  return snapshot;
}

function assertStrictSource(input: unknown): ClassifierInputSource {
  const snapshot = snapshotOwnDataProperties(input);

  for (const key of Object.keys(snapshot)) {
    if (!SOURCE_FIELD_SET.has(key)) fail("$", "contains a field that is not allowed");
  }
  for (const field of SOURCE_FIELDS) {
    if (!Object.hasOwn(snapshot, field)) fail(`$.${field}`, "field is required");
  }

  return {
    candidateId: readRequiredString(snapshot, "candidateId"),
    title: readRequiredString(snapshot, "title"),
    companyName: readRequiredString(snapshot, "companyName"),
    descriptionHtml: readRequiredString(snapshot, "descriptionHtml"),
    selectedDescriptionLocale: readRequiredString(snapshot, "selectedDescriptionLocale"),
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

  const payload: ClassifierInputV1 = Object.freeze({
    schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    candidateId: normalizedRequiredText(source.candidateId, "$.candidateId"),
    title: normalizedRequiredText(source.title, "$.title"),
    companyName: normalizedRequiredText(source.companyName, "$.companyName"),
    descriptionText,
  });
  const sidecar: ClassifierInputSidecarV1 = Object.freeze({
    selectedDescriptionLocale: normalizedRequiredText(
      source.selectedDescriptionLocale,
      "$.selectedDescriptionLocale",
    ),
    truncated,
  });

  return Object.freeze({
    payload,
    sidecar,
    contentIdentity: contentIdentityFor(payload),
  });
}
