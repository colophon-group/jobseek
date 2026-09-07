import { createHash, randomUUID } from "node:crypto";
import {
  constants as fsConstants,
  link,
  lstat,
  open,
  realpath,
  unlink,
  type FileHandle,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
  normalizeClassifierInputV1,
  type ClassifierInputSource,
  type ClassifierInputV1,
} from "../classifier-input";

export const STAGE_A_EXAMPLE_SCHEMA_VERSION = "ai-filter-stage-a-example-v1" as const;
export const STAGE_A_WIP_SCHEMA_VERSION = "ai-filter-stage-a-wip-v1" as const;
export const STAGE_A_READY_POLICY_SCHEMA_VERSION =
  "ai-filter-stage-a-ready-policy-v1" as const;
export const STAGE_A_MANIFEST_SCHEMA_VERSION = "ai-filter-stage-a-manifest-v1" as const;
export const STAGE_A_FREEZE_SCHEMA_VERSION = "ai-filter-stage-a-freeze-v1" as const;
export const STAGE_A_REQUIRED_EXAMPLES = 200;
export const STAGE_A_REQUIRED_DOUBLE_LABELS = 50;
export const STAGE_A_REPORT_MIN_CELL_SIZE = 10;
export const STAGE_A_REPOSITORY_STAGING_PATH =
  "apps/web/.private/ai-filter-evaluation" as const;

const MAX_INPUT_FILE_BYTES = 10 * 1024 * 1024;
const DIGEST_PATTERN = /^[a-f0-9]{64}$/u;
const EVAL_ID_PATTERN = /^eval-[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u;
const SAFE_FILE_NAME_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/u;
const LOCALES = ["de", "en", "fr", "it"] as const;
const SCENARIOS = [
  "clear_match",
  "clear_non_match",
  "ambiguous",
  "prompt_injection",
] as const;

export type StageALocale = (typeof LOCALES)[number];
export type StageAScenario = (typeof SCENARIOS)[number];
export type BinaryLabel = 0 | 1;

export type StageAAnnotationV1 = {
  readonly annotationId: string;
  readonly actorId: string;
  readonly label: BinaryLabel;
};

export type StageAAdjudicationV1 = {
  readonly adjudicationId: string;
  readonly actorId: string;
  readonly label: BinaryLabel;
};

export type StageAExampleV1 = {
  readonly schemaVersion: typeof STAGE_A_EXAMPLE_SCHEMA_VERSION;
  readonly exampleId: string;
  readonly softQuery: string;
  readonly queryOrigin: "eval_authored";
  readonly locale: StageALocale;
  readonly scenario: StageAScenario;
  readonly classifierSource: ClassifierInputSource;
  readonly contentIdentity: string;
  readonly annotations: readonly StageAAnnotationV1[];
  readonly adjudication: StageAAdjudicationV1 | null;
};

export type StageAWipV1 = {
  readonly schemaVersion: typeof STAGE_A_WIP_SCHEMA_VERSION;
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly examples: readonly StageAExampleV1[];
};

export type StageAReadyPolicyV1 = {
  readonly schemaVersion: typeof STAGE_A_READY_POLICY_SCHEMA_VERSION;
  readonly localeMinimums: Readonly<Record<StageALocale, number>>;
  readonly scenarioMinimums: Readonly<Record<StageAScenario, number>>;
};

type StageAReadyExampleV1 = StageAExampleV1 & {
  readonly classifierInput: ClassifierInputV1;
  readonly goldLabel: BinaryLabel;
};

export type StageAManifestV1 = {
  readonly schemaVersion: typeof STAGE_A_MANIFEST_SCHEMA_VERSION;
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly readyPolicy: StageAReadyPolicyV1;
  readonly readyPolicyDigest: string;
  readonly examples: readonly StageAReadyExampleV1[];
};

export type StageAFreezeV1 = {
  readonly schemaVersion: typeof STAGE_A_FREEZE_SCHEMA_VERSION;
  readonly manifest: StageAManifestV1;
  readonly manifestDigest: string;
};

export type StageABenchmarkExample = {
  readonly softQuery: string;
  readonly classifierInput: ClassifierInputV1;
  readonly goldLabel: BinaryLabel;
};

export type StageAReport = {
  readonly schemaVersion: "ai-filter-stage-a-report-v1";
  readonly totalExamples: number;
  readonly labelCounts:
    | Readonly<{ suppressed: true }>
    | Readonly<{ suppressed: false; negative: number; positive: number }>;
  readonly dimensions: Readonly<{
    locale: StageAReportDimension<StageALocale>;
    scenario: StageAReportDimension<StageAScenario>;
  }>;
  readonly agreement:
    | Readonly<{ suppressed: true }>
    | Readonly<{
        suppressed: false;
        doubleLabelled: number;
        agreements: number;
        disagreements: number;
      }>;
};

type StageAReportDimension<T extends string> =
  | Readonly<{ suppressed: true }>
  | Readonly<{
      suppressed: false;
      cells: readonly Readonly<{ value: T; count: number }>[];
    }>;

export class StageAEvaluationError extends Error {
  readonly path: string;
  readonly rule: string;

  constructor(pathValue: string, rule: string) {
    super(`${pathValue}: ${rule}`);
    this.name = "StageAEvaluationError";
    this.path = pathValue;
    this.rule = rule;
    this.stack = `${this.name}: ${this.message}`;
  }
}

function fail(pathValue: string, rule: string): never {
  throw new StageAEvaluationError(pathValue, rule);
}

function rawStringCompare(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function snapshotRecord(
  input: unknown,
  pathValue: string,
  allowedFields: readonly string[],
): Record<string, unknown> {
  try {
    if (typeof input !== "object" || input === null || Array.isArray(input)) {
      fail(pathValue, "object_required");
    }
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail(pathValue, "object_unreadable");
  }
  let descriptors: ReturnType<typeof Object.getOwnPropertyDescriptors>;
  try {
    descriptors = Object.getOwnPropertyDescriptors(input);
  } catch {
    fail(pathValue, "object_unreadable");
  }
  const allowed = new Set(allowedFields);
  const output = Object.create(null) as Record<string, unknown>;
  for (const key of Reflect.ownKeys(descriptors)) {
    if (typeof key !== "string" || !allowed.has(key)) {
      fail(pathValue, "additional_properties");
    }
    const descriptor = descriptors[key];
    if (!("value" in descriptor) || !descriptor.enumerable) {
      fail(pathValue, "data_properties_required");
    }
    output[key] = descriptor.value;
  }
  return output;
}

function snapshotArray(
  input: unknown,
  pathValue: string,
  minimumLength: number,
  maximumLength: number,
): unknown[] {
  try {
    if (!Array.isArray(input)) fail(pathValue, "array_required");
  } catch {
    fail(pathValue, "array_unreadable");
  }
  let descriptors: ReturnType<typeof Object.getOwnPropertyDescriptors>;
  try {
    descriptors = Object.getOwnPropertyDescriptors(input);
  } catch {
    fail(pathValue, "array_unreadable");
  }
  const lengthDescriptor = descriptors.length;
  if (
    !lengthDescriptor ||
    !("value" in lengthDescriptor) ||
    !Number.isSafeInteger(lengthDescriptor.value)
  ) {
    fail(pathValue, "dense_array_required");
  }
  const length = lengthDescriptor.value as number;
  if (length < minimumLength || length > maximumLength) {
    fail(pathValue, "bounded_array_required");
  }
  const output: unknown[] = [];
  for (const key of Reflect.ownKeys(descriptors)) {
    if (key === "length") continue;
    if (typeof key !== "string" || !/^(?:0|[1-9]\d*)$/u.test(key)) {
      fail(pathValue, "additional_properties");
    }
    const index = Number(key);
    const descriptor = descriptors[key];
    if (index >= length || !("value" in descriptor) || !descriptor.enumerable) {
      fail(pathValue, "dense_array_required");
    }
    output[index] = descriptor.value;
  }
  if (output.length !== length) fail(pathValue, "dense_array_required");
  for (let index = 0; index < length; index += 1) {
    if (!Object.hasOwn(output, index)) fail(pathValue, "dense_array_required");
  }
  return output;
}

function required(record: Record<string, unknown>, field: string, pathValue: string): unknown {
  if (!Object.hasOwn(record, field)) fail(pathValue, "required");
  return record[field];
}

function requiredString(
  record: Record<string, unknown>,
  field: string,
  pathValue: string,
  maximumCodePoints: number,
): string {
  const value = required(record, field, pathValue);
  if (typeof value !== "string") fail(pathValue, "string_required");
  if (value.length === 0) fail(pathValue, "nonempty_required");
  if (Array.from(value).length > maximumCodePoints) fail(pathValue, "string_too_long");
  if (value.includes("\0")) fail(pathValue, "nul_forbidden");
  return value;
}

function requiredLiteral<T extends string>(
  record: Record<string, unknown>,
  field: string,
  pathValue: string,
  literal: T,
): T {
  const value = required(record, field, pathValue);
  if (value !== literal) fail(pathValue, "literal_required");
  return literal;
}

function requiredEnum<T extends string>(
  record: Record<string, unknown>,
  field: string,
  pathValue: string,
  values: readonly T[],
): T {
  const value = required(record, field, pathValue);
  if (typeof value !== "string" || !values.includes(value as T)) {
    fail(pathValue, "enum_required");
  }
  return value as T;
}

function validateEvalId(value: string, pathValue: string): string {
  if (!EVAL_ID_PATTERN.test(value)) fail(pathValue, "eval_id_required");
  return value;
}

function validateDigest(value: unknown, pathValue: string): string {
  if (typeof value !== "string" || !DIGEST_PATTERN.test(value)) {
    fail(pathValue, "sha256_required");
  }
  return value;
}

function validateLabel(value: unknown, pathValue: string): BinaryLabel {
  if (value !== 0 && value !== 1) fail(pathValue, "binary_label_required");
  return value;
}

function validateAnnotation(input: unknown, pathValue: string): StageAAnnotationV1 {
  const record = snapshotRecord(input, pathValue, ["annotationId", "actorId", "label"]);
  return Object.freeze({
    annotationId: validateEvalId(
      requiredString(record, "annotationId", `${pathValue}.annotationId`, 68),
      `${pathValue}.annotationId`,
    ),
    actorId: validateEvalId(
      requiredString(record, "actorId", `${pathValue}.actorId`, 68),
      `${pathValue}.actorId`,
    ),
    label: validateLabel(required(record, "label", `${pathValue}.label`), `${pathValue}.label`),
  });
}

function validateAdjudication(input: unknown, pathValue: string): StageAAdjudicationV1 | null {
  if (input === null) return null;
  const record = snapshotRecord(input, pathValue, ["adjudicationId", "actorId", "label"]);
  return Object.freeze({
    adjudicationId: validateEvalId(
      requiredString(record, "adjudicationId", `${pathValue}.adjudicationId`, 68),
      `${pathValue}.adjudicationId`,
    ),
    actorId: validateEvalId(
      requiredString(record, "actorId", `${pathValue}.actorId`, 68),
      `${pathValue}.actorId`,
    ),
    label: validateLabel(required(record, "label", `${pathValue}.label`), `${pathValue}.label`),
  });
}

function validateClassifierSource(input: unknown, pathValue: string): ClassifierInputSource {
  const fields = [
    "candidateId",
    "title",
    "companyName",
    "descriptionHtml",
    "selectedDescriptionLocale",
  ] as const;
  const record = snapshotRecord(input, pathValue, fields);
  return Object.freeze({
    candidateId: requiredString(record, "candidateId", `${pathValue}.candidateId`, 256),
    title: requiredString(record, "title", `${pathValue}.title`, 1_000),
    companyName: requiredString(record, "companyName", `${pathValue}.companyName`, 1_000),
    descriptionHtml: requiredString(
      record,
      "descriptionHtml",
      `${pathValue}.descriptionHtml`,
      2_000_000,
    ),
    selectedDescriptionLocale: requiredEnum(
      record,
      "selectedDescriptionLocale",
      `${pathValue}.selectedDescriptionLocale`,
      LOCALES,
    ),
  });
}

function validateFrozenClassifierInput(input: unknown, pathValue: string): void {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion",
    "candidateId",
    "title",
    "companyName",
    "descriptionText",
  ]);
  requiredLiteral(
    record,
    "schemaVersion",
    `${pathValue}.schemaVersion`,
    CLASSIFIER_INPUT_SCHEMA_VERSION,
  );
  requiredString(record, "candidateId", `${pathValue}.candidateId`, 1_000);
  requiredString(record, "title", `${pathValue}.title`, 1_000);
  requiredString(record, "companyName", `${pathValue}.companyName`, 1_000);
  requiredString(record, "descriptionText", `${pathValue}.descriptionText`, 12_000);
}

function normalizeClassifierSource(source: ClassifierInputSource, pathValue: string) {
  try {
    return normalizeClassifierInputV1(source);
  } catch {
    fail(pathValue, "classifier_input_invalid");
  }
}

function validateExample(input: unknown, pathValue: string): StageAExampleV1 {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion",
    "exampleId",
    "softQuery",
    "queryOrigin",
    "locale",
    "scenario",
    "classifierSource",
    "contentIdentity",
    "annotations",
    "adjudication",
  ]);
  requiredLiteral(
    record,
    "schemaVersion",
    `${pathValue}.schemaVersion`,
    STAGE_A_EXAMPLE_SCHEMA_VERSION,
  );
  const softQuery = requiredString(record, "softQuery", `${pathValue}.softQuery`, 2_000);
  const canonicalQuery = softQuery.normalize("NFC").replace(/\s+/gu, " ").trim();
  if (
    softQuery !== canonicalQuery ||
    /(?:\p{Cc}|\p{Default_Ignorable_Code_Point})/u.test(softQuery)
  ) {
    fail(`${pathValue}.softQuery`, "canonical_query_required");
  }

  const annotationsValue = snapshotArray(
    required(record, "annotations", `${pathValue}.annotations`),
    `${pathValue}.annotations`,
    1,
    2,
  );
  const annotations = annotationsValue
    .map((annotation, index) =>
      validateAnnotation(annotation, `${pathValue}.annotations[${index}]`),
    )
    .sort((left, right) => rawStringCompare(left.annotationId, right.annotationId));
  if (new Set(annotations.map((annotation) => annotation.annotationId)).size !== annotations.length) {
    fail(`${pathValue}.annotations`, "unique_annotation_ids_required");
  }
  if (new Set(annotations.map((annotation) => annotation.actorId)).size !== annotations.length) {
    fail(`${pathValue}.annotations`, "independent_actors_required");
  }

  const classifierSource = validateClassifierSource(
    required(record, "classifierSource", `${pathValue}.classifierSource`),
    `${pathValue}.classifierSource`,
  );
  const normalized = normalizeClassifierSource(classifierSource, `${pathValue}.classifierSource`);
  const contentIdentity = validateDigest(
    required(record, "contentIdentity", `${pathValue}.contentIdentity`),
    `${pathValue}.contentIdentity`,
  );
  if (contentIdentity !== normalized.contentIdentity) {
    fail(`${pathValue}.contentIdentity`, "content_identity_mismatch");
  }

  const adjudication = validateAdjudication(
    required(record, "adjudication", `${pathValue}.adjudication`),
    `${pathValue}.adjudication`,
  );
  const disagreement =
    annotations.length === 2 && annotations[0].label !== annotations[1].label;
  if (adjudication && !disagreement) {
    fail(`${pathValue}.adjudication`, "adjudication_only_for_disagreement");
  }
  if (adjudication && annotations.some(({ actorId }) => actorId === adjudication.actorId)) {
    fail(`${pathValue}.adjudication`, "independent_adjudicator_required");
  }

  return Object.freeze({
    schemaVersion: STAGE_A_EXAMPLE_SCHEMA_VERSION,
    exampleId: validateEvalId(
      requiredString(record, "exampleId", `${pathValue}.exampleId`, 68),
      `${pathValue}.exampleId`,
    ),
    softQuery,
    queryOrigin: requiredLiteral(
      record,
      "queryOrigin",
      `${pathValue}.queryOrigin`,
      "eval_authored",
    ),
    locale: requiredEnum(record, "locale", `${pathValue}.locale`, LOCALES),
    scenario: requiredEnum(record, "scenario", `${pathValue}.scenario`, SCENARIOS),
    classifierSource,
    contentIdentity,
    annotations: Object.freeze(annotations),
    adjudication,
  });
}

export function validateStageAWip(input: unknown): StageAWipV1 {
  const record = snapshotRecord(input, "$", [
    "schemaVersion",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "examples",
  ]);
  requiredLiteral(record, "schemaVersion", "$.schemaVersion", STAGE_A_WIP_SCHEMA_VERSION);
  requiredLiteral(
    record,
    "classifierInputSchemaVersion",
    "$.classifierInputSchemaVersion",
    CLASSIFIER_INPUT_SCHEMA_VERSION,
  );
  requiredLiteral(
    record,
    "classifierInputNormalizerVersion",
    "$.classifierInputNormalizerVersion",
    CLASSIFIER_INPUT_NORMALIZER_VERSION,
  );
  const examplesValue = snapshotArray(
    required(record, "examples", "$.examples"),
    "$.examples",
    0,
    STAGE_A_REQUIRED_EXAMPLES,
  );
  const examples = examplesValue
    .map((example, index) => validateExample(example, `$.examples[${index}]`))
    .sort((left, right) => rawStringCompare(left.exampleId, right.exampleId));
  if (new Set(examples.map(({ exampleId }) => exampleId)).size !== examples.length) {
    fail("$.examples", "unique_example_ids_required");
  }
  const workIds = examples.flatMap((example) => [
    ...example.annotations.map(({ annotationId }) => annotationId),
    ...(example.adjudication ? [example.adjudication.adjudicationId] : []),
  ]);
  if (new Set(workIds).size !== workIds.length) {
    fail("$.examples", "unique_work_ids_required");
  }
  return Object.freeze({
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: validateEvalId(
      requiredString(record, "datasetId", "$.datasetId", 68),
      "$.datasetId",
    ),
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    examples: Object.freeze(examples),
  });
}

function validateMinimumMap<T extends string>(
  input: unknown,
  pathValue: string,
  keys: readonly T[],
): Readonly<Record<T, number>> {
  const record = snapshotRecord(input, pathValue, keys);
  const output = Object.create(null) as Record<T, number>;
  for (const key of keys) {
    const value = required(record, key, `${pathValue}.${key}`);
    if (!Number.isSafeInteger(value) || (value as number) < 1 || (value as number) > 200) {
      fail(`${pathValue}.${key}`, "coverage_minimum_required");
    }
    output[key] = value as number;
  }
  return Object.freeze({ ...output });
}

export function validateStageAReadyPolicy(input: unknown): StageAReadyPolicyV1 {
  const record = snapshotRecord(input, "$policy", [
    "schemaVersion",
    "localeMinimums",
    "scenarioMinimums",
  ]);
  requiredLiteral(
    record,
    "schemaVersion",
    "$policy.schemaVersion",
    STAGE_A_READY_POLICY_SCHEMA_VERSION,
  );
  const localeMinimums = validateMinimumMap(
    required(record, "localeMinimums", "$policy.localeMinimums"),
    "$policy.localeMinimums",
    LOCALES,
  );
  const scenarioMinimums = validateMinimumMap(
    required(record, "scenarioMinimums", "$policy.scenarioMinimums"),
    "$policy.scenarioMinimums",
    SCENARIOS,
  );
  if (Object.values(localeMinimums).reduce((sum, value) => sum + value, 0) > 200) {
    fail("$policy.localeMinimums", "coverage_minimums_impossible");
  }
  if (Object.values(scenarioMinimums).reduce((sum, value) => sum + value, 0) > 200) {
    fail("$policy.scenarioMinimums", "coverage_minimums_impossible");
  }
  return Object.freeze({
    schemaVersion: STAGE_A_READY_POLICY_SCHEMA_VERSION,
    localeMinimums,
    scenarioMinimums,
  });
}

function canonicalValue(input: unknown): unknown {
  if (input === null || typeof input === "string" || typeof input === "boolean") return input;
  if (typeof input === "number") {
    if (!Number.isSafeInteger(input)) fail("$", "canonical_integer_required");
    return input;
  }
  if (Array.isArray(input)) return input.map(canonicalValue);
  if (typeof input === "object") {
    const output = Object.create(null) as Record<string, unknown>;
    for (const key of Object.keys(input).sort(rawStringCompare)) {
      const value = (input as Record<string, unknown>)[key];
      if (value === undefined) fail("$", "canonical_json_value_required");
      output[key] = canonicalValue(value);
    }
    return output;
  }
  fail("$", "canonical_json_value_required");
}

export function canonicalStageAJson(input: unknown): string {
  return `${JSON.stringify(canonicalValue(input))}\n`;
}

function domainDigest(domain: string, input: unknown): string {
  return createHash("sha256")
    .update(`${domain}\n`, "utf8")
    .update(canonicalStageAJson(input), "utf8")
    .digest("hex");
}

export function digestStageAReadyPolicy(input: unknown): string {
  return domainDigest(
    STAGE_A_READY_POLICY_SCHEMA_VERSION,
    validateStageAReadyPolicy(input),
  );
}

function derivedGold(example: StageAExampleV1, pathValue: string): BinaryLabel {
  if (example.annotations.length === 1) return example.annotations[0].label;
  const [left, right] = example.annotations;
  if (left.label === right.label) return left.label;
  if (!example.adjudication) fail(`${pathValue}.adjudication`, "unresolved_disagreement");
  return example.adjudication.label;
}

function assertCoverage<T extends string>(
  examples: readonly StageAExampleV1[],
  field: "locale" | "scenario",
  minimums: Readonly<Record<T, number>>,
  keys: readonly T[],
): void {
  for (const key of keys) {
    const count = examples.filter((example) => example[field] === key).length;
    if (count < minimums[key]) fail(`$policy.${field}Minimums.${key}`, "coverage_not_met");
  }
}

export function buildStageAReadyManifest(
  wipInput: unknown,
  policyInput: unknown,
  expectedPolicyDigest: string,
): StageAManifestV1 {
  validateDigest(expectedPolicyDigest, "$expectedPolicyDigest");
  const wip = validateStageAWip(wipInput);
  const readyPolicy = validateStageAReadyPolicy(policyInput);
  const readyPolicyDigest = digestStageAReadyPolicy(readyPolicy);
  if (readyPolicyDigest !== expectedPolicyDigest) {
    fail("$expectedPolicyDigest", "policy_digest_mismatch");
  }
  if (wip.examples.length !== STAGE_A_REQUIRED_EXAMPLES) {
    fail("$.examples", "exactly_200_examples_required");
  }

  const pairKeys = wip.examples.map(
    ({ softQuery, contentIdentity }) => `${softQuery.length}:${softQuery}${contentIdentity}`,
  );
  if (new Set(pairKeys).size !== pairKeys.length) {
    fail("$.examples", "unique_semantic_pairs_required");
  }
  const doubleLabelled = wip.examples.filter(({ annotations }) => annotations.length === 2).length;
  if (doubleLabelled < STAGE_A_REQUIRED_DOUBLE_LABELS) {
    fail("$.examples", "at_least_50_double_labels_required");
  }
  assertCoverage(wip.examples, "locale", readyPolicy.localeMinimums, LOCALES);
  assertCoverage(wip.examples, "scenario", readyPolicy.scenarioMinimums, SCENARIOS);

  const examples: StageAReadyExampleV1[] = wip.examples.map((example, index) => {
    const normalized = normalizeClassifierSource(
      example.classifierSource,
      `$.examples[${index}].classifierSource`,
    );
    return Object.freeze({
      ...example,
      classifierInput: normalized.payload,
      goldLabel: derivedGold(example, `$.examples[${index}]`),
    });
  });
  return Object.freeze({
    schemaVersion: STAGE_A_MANIFEST_SCHEMA_VERSION,
    datasetId: wip.datasetId,
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    readyPolicy,
    readyPolicyDigest,
    examples: Object.freeze(examples),
  });
}

export function freezeStageA(
  wipInput: unknown,
  policyInput: unknown,
  expectedPolicyDigest: string,
): StageAFreezeV1 {
  const manifest = buildStageAReadyManifest(wipInput, policyInput, expectedPolicyDigest);
  return Object.freeze({
    schemaVersion: STAGE_A_FREEZE_SCHEMA_VERSION,
    manifest,
    manifestDigest: domainDigest(STAGE_A_MANIFEST_SCHEMA_VERSION, manifest),
  });
}

function baseExampleFromFrozen(input: unknown, pathValue: string): unknown {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion",
    "exampleId",
    "softQuery",
    "queryOrigin",
    "locale",
    "scenario",
    "classifierSource",
    "contentIdentity",
    "annotations",
    "adjudication",
    "classifierInput",
    "goldLabel",
  ]);
  validateFrozenClassifierInput(
    required(record, "classifierInput", `${pathValue}.classifierInput`),
    `${pathValue}.classifierInput`,
  );
  validateLabel(required(record, "goldLabel", `${pathValue}.goldLabel`), `${pathValue}.goldLabel`);
  return {
    schemaVersion: required(record, "schemaVersion", `${pathValue}.schemaVersion`),
    exampleId: required(record, "exampleId", `${pathValue}.exampleId`),
    softQuery: required(record, "softQuery", `${pathValue}.softQuery`),
    queryOrigin: required(record, "queryOrigin", `${pathValue}.queryOrigin`),
    locale: required(record, "locale", `${pathValue}.locale`),
    scenario: required(record, "scenario", `${pathValue}.scenario`),
    classifierSource: required(record, "classifierSource", `${pathValue}.classifierSource`),
    contentIdentity: required(record, "contentIdentity", `${pathValue}.contentIdentity`),
    annotations: required(record, "annotations", `${pathValue}.annotations`),
    adjudication: required(record, "adjudication", `${pathValue}.adjudication`),
  };
}

function validateFrozen(
  input: unknown,
  expectedManifestDigest: string,
  expectedPolicyDigest: string,
): StageAFreezeV1 {
  validateDigest(expectedManifestDigest, "$expectedManifestDigest");
  validateDigest(expectedPolicyDigest, "$expectedPolicyDigest");
  const envelope = snapshotRecord(input, "$", ["schemaVersion", "manifest", "manifestDigest"]);
  requiredLiteral(
    envelope,
    "schemaVersion",
    "$.schemaVersion",
    STAGE_A_FREEZE_SCHEMA_VERSION,
  );
  const manifestInput = required(envelope, "manifest", "$.manifest");
  const manifestRecord = snapshotRecord(manifestInput, "$.manifest", [
    "schemaVersion",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "readyPolicy",
    "readyPolicyDigest",
    "examples",
  ]);
  requiredLiteral(
    manifestRecord,
    "schemaVersion",
    "$.manifest.schemaVersion",
    STAGE_A_MANIFEST_SCHEMA_VERSION,
  );
  const examplesInput = snapshotArray(
    required(manifestRecord, "examples", "$.manifest.examples"),
    "$.manifest.examples",
    STAGE_A_REQUIRED_EXAMPLES,
    STAGE_A_REQUIRED_EXAMPLES,
  );
  const wip = {
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: required(manifestRecord, "datasetId", "$.manifest.datasetId"),
    classifierInputSchemaVersion: required(
      manifestRecord,
      "classifierInputSchemaVersion",
      "$.manifest.classifierInputSchemaVersion",
    ),
    classifierInputNormalizerVersion: required(
      manifestRecord,
      "classifierInputNormalizerVersion",
      "$.manifest.classifierInputNormalizerVersion",
    ),
    examples: examplesInput.map((example, index) =>
      baseExampleFromFrozen(example, `$.manifest.examples[${index}]`),
    ),
  };
  const readyPolicy = required(manifestRecord, "readyPolicy", "$.manifest.readyPolicy");
  const embeddedPolicyDigest = validateDigest(
    required(manifestRecord, "readyPolicyDigest", "$.manifest.readyPolicyDigest"),
    "$.manifest.readyPolicyDigest",
  );
  if (embeddedPolicyDigest !== expectedPolicyDigest) {
    fail("$.manifest.readyPolicyDigest", "policy_digest_mismatch");
  }
  const rebuiltManifest = buildStageAReadyManifest(wip, readyPolicy, expectedPolicyDigest);
  if (canonicalStageAJson(rebuiltManifest) !== canonicalStageAJson(manifestInput)) {
    fail("$.manifest", "derived_manifest_mismatch");
  }
  const manifestDigest = validateDigest(
    required(envelope, "manifestDigest", "$.manifestDigest"),
    "$.manifestDigest",
  );
  const recomputedDigest = domainDigest(STAGE_A_MANIFEST_SCHEMA_VERSION, rebuiltManifest);
  if (manifestDigest !== recomputedDigest || manifestDigest !== expectedManifestDigest) {
    fail("$.manifestDigest", "manifest_digest_mismatch");
  }
  return Object.freeze({
    schemaVersion: STAGE_A_FREEZE_SCHEMA_VERSION,
    manifest: rebuiltManifest,
    manifestDigest,
  });
}

function duplicateSafeJsonParse(text: string): unknown {
  let offset = 0;
  const whitespace = /\s/u;
  const skipWhitespace = () => {
    while (offset < text.length && whitespace.test(text[offset])) offset += 1;
  };
  const parseStringToken = (): string => {
    const start = offset;
    if (text[offset] !== '"') fail("$file", "invalid_json");
    offset += 1;
    while (offset < text.length) {
      if (text[offset] === '"') {
        offset += 1;
        try {
          return JSON.parse(text.slice(start, offset)) as string;
        } catch {
          fail("$file", "invalid_json");
        }
      }
      if (text[offset] === "\\") offset += 1;
      offset += 1;
    }
    fail("$file", "invalid_json");
  };
  const parseValue = (): void => {
    skipWhitespace();
    const token = text[offset];
    if (token === '"') {
      parseStringToken();
      return;
    }
    if (token === "{") {
      offset += 1;
      skipWhitespace();
      const keys = new Set<string>();
      if (text[offset] === "}") {
        offset += 1;
        return;
      }
      while (offset < text.length) {
        skipWhitespace();
        const key = parseStringToken();
        if (keys.has(key)) fail("$file", "duplicate_json_key");
        keys.add(key);
        skipWhitespace();
        if (text[offset] !== ":") fail("$file", "invalid_json");
        offset += 1;
        parseValue();
        skipWhitespace();
        if (text[offset] === "}") {
          offset += 1;
          return;
        }
        if (text[offset] !== ",") fail("$file", "invalid_json");
        offset += 1;
      }
      fail("$file", "invalid_json");
    }
    if (token === "[") {
      offset += 1;
      skipWhitespace();
      if (text[offset] === "]") {
        offset += 1;
        return;
      }
      while (offset < text.length) {
        parseValue();
        skipWhitespace();
        if (text[offset] === "]") {
          offset += 1;
          return;
        }
        if (text[offset] !== ",") fail("$file", "invalid_json");
        offset += 1;
      }
      fail("$file", "invalid_json");
    }
    const match = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/u.exec(
      text.slice(offset),
    );
    if (!match) fail("$file", "invalid_json");
    offset += match[0].length;
  };
  parseValue();
  skipWhitespace();
  if (offset !== text.length) fail("$file", "invalid_json");
  try {
    return JSON.parse(text) as unknown;
  } catch {
    fail("$file", "invalid_json");
  }
}

async function findRepositoryRoot(start: string): Promise<string | null> {
  let current = path.resolve(start);
  for (;;) {
    try {
      const marker = await lstat(path.join(current, "pnpm-workspace.yaml"));
      if (marker.isFile() && !marker.isSymbolicLink()) return await realpath(current);
    } catch {
      // Continue upwards without exposing host paths or filesystem errors.
    }
    const parent = path.dirname(current);
    if (parent === current) return null;
    current = parent;
  }
}

function isInside(parent: string, child: string): boolean {
  const relative = path.relative(parent, child);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

type EvaluationRoot = Readonly<{
  path: string;
  device: number;
  inode: number;
  handle: FileHandle;
}>;

async function assertStableRoot(root: EvaluationRoot): Promise<void> {
  try {
    const current = await lstat(root.path);
    const pinned = await root.handle.stat();
    if (
      !current.isDirectory() ||
      current.isSymbolicLink() ||
      current.dev !== root.device ||
      current.ino !== root.inode ||
      pinned.dev !== root.device ||
      pinned.ino !== root.inode
    ) {
      fail("$root", "root_changed");
    }
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail("$root", "root_changed");
  }
}

async function evaluationRoot(): Promise<EvaluationRoot> {
  const configured = process.env.AI_FILTER_EVAL_DATA_ROOT;
  if (!configured || !path.isAbsolute(configured)) fail("$root", "absolute_root_required");
  const resolved = path.resolve(configured);
  if (resolved === path.parse(resolved).root) fail("$root", "broad_root_forbidden");
  let rootStat: Awaited<ReturnType<typeof lstat>>;
  try {
    rootStat = await lstat(resolved);
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail("$root", "safe_directory_required");
    if ((rootStat.mode & 0o077) !== 0) fail("$root", "private_root_required");
    if (typeof process.getuid === "function" && rootStat.uid !== process.getuid()) {
      fail("$root", "owned_root_required");
    }
    const parentStat = await lstat(path.dirname(resolved));
    const parentWritable = (parentStat.mode & 0o022) !== 0;
    const parentSticky = (parentStat.mode & 0o1000) !== 0;
    if (!parentStat.isDirectory() || parentStat.isSymbolicLink() || (parentWritable && !parentSticky)) {
      fail("$root", "safe_root_parent_required");
    }
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail("$root", "safe_directory_required");
  }
  let rootReal: string;
  try {
    rootReal = await realpath(resolved);
    const parentReal = await realpath(path.dirname(resolved));
    if (parentReal !== path.dirname(rootReal)) fail("$root", "symlinked_ancestor_forbidden");
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail("$root", "safe_directory_required");
  }
  const repositoryRoot = await findRepositoryRoot(path.dirname(fileURLToPath(import.meta.url)));
  if (repositoryRoot && isInside(repositoryRoot, rootReal)) {
    const allowed = path.join(repositoryRoot, STAGE_A_REPOSITORY_STAGING_PATH);
    if (rootReal !== allowed) fail("$root", "repository_staging_path_required");
  }
  let rootHandle: FileHandle;
  try {
    rootHandle = await open(
      rootReal,
      fsConstants.O_RDONLY | fsConstants.O_DIRECTORY | fsConstants.O_NOFOLLOW,
    );
  } catch {
    fail("$root", "safe_directory_required");
  }
  const root = Object.freeze({
    path: rootReal,
    device: rootStat.dev,
    inode: rootStat.ino,
    handle: rootHandle,
  });
  try {
    await assertStableRoot(root);
  } catch (error) {
    await rootHandle.close().catch(() => undefined);
    throw error;
  }
  return root;
}

function safeFileName(relativePath: string): string {
  if (
    typeof relativePath !== "string" ||
    relativePath.length === 0 ||
    path.isAbsolute(relativePath) ||
    !SAFE_FILE_NAME_PATTERN.test(relativePath) ||
    relativePath === "." ||
    relativePath === ".."
  ) {
    fail("$file", "safe_file_name_required");
  }
  return relativePath;
}

async function readPrivateJson(relativePath: string): Promise<unknown> {
  const fileName = safeFileName(relativePath);
  const root = await evaluationRoot();
  const candidate = path.join(root.path, fileName);
  let handle;
  try {
    await assertStableRoot(root);
    handle = await open(
      candidate,
      fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK,
    );
    const stat = await handle.stat();
    if (!stat.isFile()) fail("$file", "regular_file_required");
    if (stat.nlink !== 1) fail("$file", "single_link_file_required");
    if (stat.size > MAX_INPUT_FILE_BYTES) fail("$file", "file_too_large");
    const bytes = await handle.readFile();
    await assertStableRoot(root);
    if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
      fail("$file", "canonical_utf8_required");
    }
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      fail("$file", "canonical_utf8_required");
    }
    return duplicateSafeJsonParse(text);
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail("$file", "file_unavailable");
  } finally {
    await handle?.close().catch(() => undefined);
    await root.handle.close().catch(() => undefined);
  }
}

async function publishPrivateFile(relativePath: string, bytes: string): Promise<void> {
  const fileName = safeFileName(relativePath);
  if (Buffer.byteLength(bytes, "utf8") > MAX_INPUT_FILE_BYTES) {
    fail("$file", "file_too_large");
  }
  const root = await evaluationRoot();
  const destination = path.join(root.path, fileName);
  const temporary = path.join(root.path, `.stage-a-${randomUUID()}.tmp`);
  let handle;
  try {
    await assertStableRoot(root);
    handle = await open(
      temporary,
      fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_WRONLY | fsConstants.O_NOFOLLOW,
      0o600,
    );
    await handle.writeFile(bytes, "utf8");
    await handle.chmod(0o600);
    await handle.sync();
    await handle.close();
    handle = undefined;
    await assertStableRoot(root);
    await link(temporary, destination);
    await assertStableRoot(root);
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail("$file", "exclusive_publish_failed");
  } finally {
    await handle?.close().catch(() => undefined);
    await unlink(temporary).catch(() => undefined);
    await root.handle.close().catch(() => undefined);
  }
}

export async function readStageAWipFile(relativePath: string): Promise<StageAWipV1> {
  return validateStageAWip(await readPrivateJson(relativePath));
}

export async function writeStageAFreezeFile(
  relativePath: string,
  wipInput: unknown,
  policyInput: unknown,
  expectedPolicyDigest: string,
): Promise<Readonly<{ manifestDigest: string }>> {
  const frozen = freezeStageA(wipInput, policyInput, expectedPolicyDigest);
  await publishPrivateFile(relativePath, canonicalStageAJson(frozen));
  return Object.freeze({ manifestDigest: frozen.manifestDigest });
}

async function readValidatedFreeze(
  relativePath: string,
  expectedManifestDigest: string,
  expectedPolicyDigest: string,
): Promise<StageAFreezeV1> {
  return validateFrozen(
    await readPrivateJson(relativePath),
    expectedManifestDigest,
    expectedPolicyDigest,
  );
}

function deepFreeze<T>(input: T): T {
  if (typeof input !== "object" || input === null || Object.isFrozen(input)) return input;
  for (const value of Object.values(input)) deepFreeze(value);
  return Object.freeze(input);
}

export async function loadStageABenchmark(
  relativePath: string,
  expectedManifestDigest: string,
  expectedPolicyDigest: string,
): Promise<readonly StageABenchmarkExample[]> {
  const frozen = await readValidatedFreeze(
    relativePath,
    expectedManifestDigest,
    expectedPolicyDigest,
  );
  return deepFreeze(
    frozen.manifest.examples.map(({ softQuery, classifierInput, goldLabel }) => ({
      softQuery,
      classifierInput: { ...classifierInput },
      goldLabel,
    })),
  );
}

function reportDimension<T extends string>(
  values: readonly T[],
  examples: readonly StageAReadyExampleV1[],
  field: "locale" | "scenario",
): StageAReportDimension<T> {
  const cells = values.map((value) => ({
    value,
    count: examples.filter((example) => example[field] === value).length,
  }));
  if (cells.some(({ count }) => count < STAGE_A_REPORT_MIN_CELL_SIZE)) {
    return Object.freeze({ suppressed: true });
  }
  return deepFreeze({ suppressed: false, cells });
}

export async function reportStageAFreeze(
  relativePath: string,
  expectedManifestDigest: string,
  expectedPolicyDigest: string,
): Promise<StageAReport> {
  const frozen = await readValidatedFreeze(
    relativePath,
    expectedManifestDigest,
    expectedPolicyDigest,
  );
  const examples = frozen.manifest.examples;
  const doubles = examples.filter(({ annotations }) => annotations.length === 2);
  const agreements = doubles.filter(
    ({ annotations }) => annotations[0].label === annotations[1].label,
  ).length;
  const disagreements = doubles.length - agreements;
  const agreement =
    agreements < STAGE_A_REPORT_MIN_CELL_SIZE || disagreements < STAGE_A_REPORT_MIN_CELL_SIZE
      ? Object.freeze({ suppressed: true as const })
      : Object.freeze({
          suppressed: false as const,
          doubleLabelled: doubles.length,
          agreements,
          disagreements,
        });
  const negative = examples.filter(({ goldLabel }) => goldLabel === 0).length;
  const positive = examples.length - negative;
  const labelCounts =
    negative < STAGE_A_REPORT_MIN_CELL_SIZE || positive < STAGE_A_REPORT_MIN_CELL_SIZE
      ? Object.freeze({ suppressed: true as const })
      : Object.freeze({ suppressed: false as const, negative, positive });
  return deepFreeze({
    schemaVersion: "ai-filter-stage-a-report-v1" as const,
    totalExamples: examples.length,
    labelCounts,
    dimensions: {
      locale: reportDimension(LOCALES, examples, "locale"),
      scenario: reportDimension(SCENARIOS, examples, "scenario"),
    },
    agreement,
  });
}

export async function readStageAReadyPolicyFile(
  relativePath: string,
): Promise<StageAReadyPolicyV1> {
  return validateStageAReadyPolicy(await readPrivateJson(relativePath));
}
