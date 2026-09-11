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
  CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT,
  CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT,
  CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT,
  CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
  normalizeClassifierInputV1,
  type ClassifierInputSource,
  type ClassifierInputV1,
} from "../classifier-input";
import {
  AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
  normalizeAiFilterSoftQueryV1,
  parseAiFilterCandidateId,
} from "../contract";

export const STAGE_A_FILTER_SCHEMA_VERSION = "ai-filter-stage-a-filter-v2" as const;
export const STAGE_A_BUNDLE_SCHEMA_VERSION = "ai-filter-stage-a-bundle-v2" as const;
export const STAGE_A_PAIR_SCHEMA_VERSION = "ai-filter-stage-a-pair-v2" as const;
export const STAGE_A_WIP_SCHEMA_VERSION = "ai-filter-stage-a-wip-v2" as const;
export const STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION =
  "ai-filter-stage-a-silver-manifest-v2" as const;
export const STAGE_A_SILVER_FREEZE_SCHEMA_VERSION =
  "ai-filter-stage-a-silver-freeze-v2" as const;
export const STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION =
  "ai-filter-stage-a-human-audit-policy-v2" as const;
export const STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION =
  "ai-filter-stage-a-human-feedback-v2" as const;
export const STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION =
  "ai-filter-stage-a-gold-manifest-v2" as const;
export const STAGE_A_GOLD_FREEZE_SCHEMA_VERSION =
  "ai-filter-stage-a-gold-freeze-v2" as const;
export const STAGE_A_REQUIRED_FILTERS = 20;
export const STAGE_A_REQUIRED_BUNDLES = 25;
export const STAGE_A_REQUIRED_PAIRS = 200;
export const STAGE_A_REQUIRED_PAIRS_PER_BUNDLE = 8;
export const STAGE_A_REQUIRED_PRODUCTION_SHAPED_BUNDLES = 15;
export const STAGE_A_REQUIRED_CHALLENGE_BUNDLES = 10;
export const STAGE_A_MAX_HUMAN_AUDIT_PAIRS = 32;
export const STAGE_A_REPORT_MIN_CELL_SIZE = 10;
export const STAGE_A_REPOSITORY_STAGING_PATH =
  "apps/web/.private/ai-filter-evaluation" as const;

const MAX_INPUT_FILE_BYTES = 10 * 1024 * 1024;
const DIGEST_PATTERN = /^[a-f0-9]{64}$/u;
const EVAL_ID_PATTERN = /^eval-[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u;
const PROVENANCE_TOKEN_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/u;
const SAFE_FILE_NAME_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/u;
const LOCALES = ["de", "en", "fr", "it"] as const;
const COHORTS = ["production_shaped", "challenge"] as const;
const PERSONAS = [
  "lazy",
  "verbose",
  "misunderstood_purpose",
  "precise",
  "vague",
  "contradictory",
  "multilingual",
] as const;
const EVIDENCE_CONDITIONS = [
  "direct_support",
  "direct_conflict",
  "insufficient_evidence",
  "policy_boundary",
  "prompt_injection",
] as const;
const LABELS = ["accept", "reject"] as const;
const REASONING_EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"] as const;
const GENERALIZED_CONTEXT_FIELDS = [
  "companyScope",
  "locationScope",
  "occupationScope",
  "keywordScope",
  "seniorityScope",
  "technologyScope",
  "workModeScope",
  "employmentTypeScope",
  "compensationScope",
  "experienceScope",
  "locale",
] as const;

export type StageALocale = (typeof LOCALES)[number];
export type StageACohort = (typeof COHORTS)[number];
export type StageAPersona = (typeof PERSONAS)[number];
export type StageAEvidenceCondition = (typeof EVIDENCE_CONDITIONS)[number];
export type StageALabel = (typeof LABELS)[number];

export type StageAGeneralizedFilterContextV2 = {
  readonly companyScope: "any" | "selected";
  readonly locationScope: "none" | "single" | "multiple" | "global";
  readonly occupationScope: "none" | "single" | "multiple";
  readonly keywordScope: "none" | "single" | "multiple";
  readonly seniorityScope: "none" | "single" | "multiple";
  readonly technologyScope: "none" | "single" | "multiple";
  readonly workModeScope: "none" | "single" | "multiple";
  readonly employmentTypeScope: "none" | "single" | "multiple";
  readonly compensationScope: "none" | "minimum" | "maximum" | "range";
  readonly experienceScope: "none" | "minimum" | "maximum" | "range";
  readonly locale: StageALocale | "other";
};

export type StageAFilterV2 = {
  readonly schemaVersion: typeof STAGE_A_FILTER_SCHEMA_VERSION;
  readonly filterId: string;
  readonly source: "production_deidentified";
  readonly sourceFilterDigest: string;
  readonly generalizedContext: StageAGeneralizedFilterContextV2;
};

export type StageAPromptProvenanceV2 = {
  readonly origin: "agent_synthetic";
  readonly authorId: string;
  readonly agentRole: string;
  readonly model: string;
  readonly modelVersion: string;
  readonly reasoningEffort: (typeof REASONING_EFFORTS)[number];
  readonly taskPromptDigest: string;
};

export type StageABundleV2 = {
  readonly schemaVersion: typeof STAGE_A_BUNDLE_SCHEMA_VERSION;
  readonly bundleId: string;
  readonly filterId: string;
  readonly cohort: StageACohort;
  readonly persona: StageAPersona;
  readonly softQuery: string;
  readonly promptProvenance: StageAPromptProvenanceV2;
};

export type StageAAnnotationV2 = {
  readonly annotationId: string;
  readonly actorId: string;
  readonly label: StageALabel;
};

export type StageAAdjudicationV2 = {
  readonly adjudicationId: string;
  readonly actorId: string;
  readonly label: StageALabel;
};

export type StageAPairV2 = {
  readonly schemaVersion: typeof STAGE_A_PAIR_SCHEMA_VERSION;
  readonly pairId: string;
  readonly bundleId: string;
  readonly locale: StageALocale;
  readonly evidenceCondition: StageAEvidenceCondition;
  readonly ambiguity: boolean;
  readonly classifierSource: ClassifierInputSource;
  readonly contentIdentity: string;
  readonly annotations: readonly [StageAAnnotationV2, StageAAnnotationV2];
  readonly adjudication: StageAAdjudicationV2 | null;
};

export type StageAFinalCriticV2 = {
  readonly reviewId: string;
  readonly actorId: string;
  readonly approved: true;
};

export type StageAWipV2 = {
  readonly schemaVersion: typeof STAGE_A_WIP_SCHEMA_VERSION;
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly softQueryNormalizerVersion: typeof AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION;
  readonly calibrationDigest: string;
  readonly filters: readonly StageAFilterV2[];
  readonly bundles: readonly StageABundleV2[];
  readonly pairs: readonly StageAPairV2[];
  readonly finalCritic: StageAFinalCriticV2;
};

export type StageASilverProvenanceV2 = {
  readonly method: "agreement" | "adjudication";
  readonly annotationIds: readonly [string, string];
  readonly adjudicationId: string | null;
};

export type StageASilverPairV2 = StageAPairV2 & {
  readonly classifierInput: ClassifierInputV1;
  readonly silverLabel: StageALabel;
  readonly silverProvenance: StageASilverProvenanceV2;
};

export type StageASilverManifestV2 = {
  readonly schemaVersion: typeof STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION;
  readonly status: "agent_adjudicated_silver";
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly softQueryNormalizerVersion: typeof AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION;
  readonly calibrationDigest: string;
  readonly sourceWipDigest: string;
  readonly filters: readonly StageAFilterV2[];
  readonly bundles: readonly StageABundleV2[];
  readonly pairs: readonly StageASilverPairV2[];
  readonly finalCritic: StageAFinalCriticV2;
};

export type StageASilverFreezeV2 = {
  readonly schemaVersion: typeof STAGE_A_SILVER_FREEZE_SCHEMA_VERSION;
  readonly manifest: StageASilverManifestV2;
  readonly silverDigest: string;
};

export type StageAHumanAuditPolicyV2 = {
  readonly schemaVersion: typeof STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION;
  readonly sourceSilverDigest: string;
  readonly auditPairIds: readonly string[];
};

export type StageAHumanFeedbackDecisionV2 = {
  readonly pairId: string;
  readonly judgment: StageALabel | "unclear";
};

export type StageAHumanFeedbackV2 = {
  readonly schemaVersion: typeof STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION;
  readonly feedbackId: string;
  readonly reviewerId: string;
  readonly sourceSilverDigest: string;
  readonly auditPolicyDigest: string;
  readonly approved: boolean;
  readonly decisions: readonly StageAHumanFeedbackDecisionV2[];
};

export type StageAGoldProvenanceV2 = {
  readonly source: "silver" | "human_correction";
  readonly sourcePairId: string;
  readonly humanFeedbackId: string | null;
};

export type StageAGoldPairV2 = StageASilverPairV2 & {
  readonly goldLabel: StageALabel;
  readonly goldProvenance: StageAGoldProvenanceV2;
};

export type StageAGoldManifestV2 = {
  readonly schemaVersion: typeof STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION;
  readonly status: "human_audited_gold";
  readonly sourceSilverDigest: string;
  readonly auditPolicyDigest: string;
  readonly humanFeedbackDigest: string;
  readonly humanFeedbackId: string;
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly softQueryNormalizerVersion: typeof AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION;
  readonly calibrationDigest: string;
  readonly filters: readonly StageAFilterV2[];
  readonly bundles: readonly StageABundleV2[];
  readonly pairs: readonly StageAGoldPairV2[];
  readonly finalCritic: StageAFinalCriticV2;
};

export type StageAGoldFreezeV2 = {
  readonly schemaVersion: typeof STAGE_A_GOLD_FREEZE_SCHEMA_VERSION;
  readonly manifest: StageAGoldManifestV2;
  readonly goldDigest: string;
};

export type StageATargetInputV2 = {
  readonly pairId: string;
  readonly query: string;
  readonly classifierInput: ClassifierInputV1;
};

export type StageAScoringLabelV2 = {
  readonly pairId: string;
  readonly label: StageALabel;
};

type StageACohortReportV2 = Readonly<{
  totalPairs: number;
  labels:
    | Readonly<{ suppressed: true }>
    | Readonly<{ suppressed: false; accept: number; reject: number }>;
}>;

export type StageAReportV2 = Readonly<{
  schemaVersion: "ai-filter-stage-a-report-v2";
  cohorts: Readonly<Record<StageACohort, StageACohortReportV2>>;
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

const SAFE_ARRAY_PROTOTYPE = Object.freeze(
  Object.defineProperty(Object.create(Array.prototype), "toJSON", {
    value(this: unknown[]) {
      return this;
    },
  }),
);

function safeArray<T>(values: readonly T[]): T[] {
  const output = Array.from(values);
  Object.setPrototypeOf(output, SAFE_ARRAY_PROTOTYPE);
  return output;
}

function frozenNullPrototypeRecord<T extends object>(values: T): T {
  return Object.freeze(Object.assign(Object.create(null), values)) as T;
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
  return value;
}

function requiredSourceString(
  record: Record<string, unknown>,
  field: string,
  pathValue: string,
  maximumCodePoints: number,
): string {
  const value = required(record, field, pathValue);
  if (typeof value !== "string") fail(pathValue, "string_required");
  // JSON Schema and this outer boundary count Unicode code points. The
  // production normalizer remains authoritative for its stricter UTF-16
  // code-unit limits and all normalized-value requirements.
  if (Array.from(value).length > maximumCodePoints) fail(pathValue, "string_too_long");
  return value;
}

function requiredLiteral<T extends string | number | boolean>(
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

function validateCandidateId(value: unknown, pathValue: string): string {
  try {
    return parseAiFilterCandidateId(value);
  } catch {
    fail(pathValue, "canonical_candidate_id_required");
  }
}

function validateSoftQuery(value: unknown, pathValue: string): string {
  let normalized: string;
  try {
    normalized = normalizeAiFilterSoftQueryV1(value);
  } catch {
    fail(pathValue, "canonical_query_required");
  }
  if (value !== normalized) fail(pathValue, "canonical_query_required");
  return normalized;
}

function validateDigest(value: unknown, pathValue: string): string {
  if (typeof value !== "string" || !DIGEST_PATTERN.test(value)) {
    fail(pathValue, "sha256_required");
  }
  return value;
}

function validateLabel(value: unknown, pathValue: string): StageALabel {
  if (typeof value !== "string" || !LABELS.includes(value as StageALabel)) {
    fail(pathValue, "label_required");
  }
  return value as StageALabel;
}

function validateAnnotation(input: unknown, pathValue: string): StageAAnnotationV2 {
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

function validateAdjudication(input: unknown, pathValue: string): StageAAdjudicationV2 | null {
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
    candidateId: validateCandidateId(
      required(record, "candidateId", `${pathValue}.candidateId`),
      `${pathValue}.candidateId`,
    ),
    title: requiredSourceString(
      record,
      "title",
      `${pathValue}.title`,
      CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
    ),
    companyName: requiredSourceString(
      record,
      "companyName",
      `${pathValue}.companyName`,
      CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
    ),
    descriptionHtml: requiredSourceString(
      record,
      "descriptionHtml",
      `${pathValue}.descriptionHtml`,
      CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT,
    ),
    selectedDescriptionLocale: requiredSourceString(
      record,
      "selectedDescriptionLocale",
      `${pathValue}.selectedDescriptionLocale`,
      CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
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
  validateCandidateId(
    required(record, "candidateId", `${pathValue}.candidateId`),
    `${pathValue}.candidateId`,
  );
  requiredString(
    record,
    "title",
    `${pathValue}.title`,
    CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT,
  );
  requiredString(
    record,
    "companyName",
    `${pathValue}.companyName`,
    CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT,
  );
  requiredString(
    record,
    "descriptionText",
    `${pathValue}.descriptionText`,
    CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT,
  );
}

function normalizeClassifierSource(source: ClassifierInputSource, pathValue: string) {
  try {
    return normalizeClassifierInputV1(source);
  } catch {
    fail(pathValue, "classifier_input_invalid");
  }
}

function requiredBoolean(
  record: Record<string, unknown>,
  field: string,
  pathValue: string,
): boolean {
  const value = required(record, field, pathValue);
  if (typeof value !== "boolean") fail(pathValue, "boolean_required");
  return value;
}

function evalIdField(
  record: Record<string, unknown>,
  field: string,
  pathValue: string,
): string {
  return validateEvalId(requiredString(record, field, pathValue, 68), pathValue);
}

function provenanceTokenField(
  record: Record<string, unknown>,
  field: string,
  pathValue: string,
): string {
  const value = requiredString(record, field, pathValue, 128);
  if (!PROVENANCE_TOKEN_PATTERN.test(value)) fail(pathValue, "provenance_token_required");
  return value;
}

function validateGeneralizedContext(
  input: unknown,
  pathValue: string,
): StageAGeneralizedFilterContextV2 {
  const record = snapshotRecord(input, pathValue, GENERALIZED_CONTEXT_FIELDS);
  return Object.freeze({
    companyScope: requiredEnum(record, "companyScope", `${pathValue}.companyScope`, ["any", "selected"]),
    locationScope: requiredEnum(record, "locationScope", `${pathValue}.locationScope`, ["none", "single", "multiple", "global"]),
    occupationScope: requiredEnum(record, "occupationScope", `${pathValue}.occupationScope`, ["none", "single", "multiple"]),
    keywordScope: requiredEnum(record, "keywordScope", `${pathValue}.keywordScope`, ["none", "single", "multiple"]),
    seniorityScope: requiredEnum(record, "seniorityScope", `${pathValue}.seniorityScope`, ["none", "single", "multiple"]),
    technologyScope: requiredEnum(record, "technologyScope", `${pathValue}.technologyScope`, ["none", "single", "multiple"]),
    workModeScope: requiredEnum(record, "workModeScope", `${pathValue}.workModeScope`, ["none", "single", "multiple"]),
    employmentTypeScope: requiredEnum(record, "employmentTypeScope", `${pathValue}.employmentTypeScope`, ["none", "single", "multiple"]),
    compensationScope: requiredEnum(record, "compensationScope", `${pathValue}.compensationScope`, ["none", "minimum", "maximum", "range"]),
    experienceScope: requiredEnum(record, "experienceScope", `${pathValue}.experienceScope`, ["none", "minimum", "maximum", "range"]),
    locale: requiredEnum(record, "locale", `${pathValue}.locale`, ["de", "en", "fr", "it", "other"]),
  });
}

function validateFilter(input: unknown, pathValue: string): StageAFilterV2 {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion",
    "filterId",
    "source",
    "sourceFilterDigest",
    "generalizedContext",
  ]);
  requiredLiteral(record, "schemaVersion", `${pathValue}.schemaVersion`, STAGE_A_FILTER_SCHEMA_VERSION);
  return Object.freeze({
    schemaVersion: STAGE_A_FILTER_SCHEMA_VERSION,
    filterId: evalIdField(record, "filterId", `${pathValue}.filterId`),
    source: requiredLiteral(record, "source", `${pathValue}.source`, "production_deidentified"),
    sourceFilterDigest: validateDigest(required(record, "sourceFilterDigest", `${pathValue}.sourceFilterDigest`), `${pathValue}.sourceFilterDigest`),
    generalizedContext: validateGeneralizedContext(required(record, "generalizedContext", `${pathValue}.generalizedContext`), `${pathValue}.generalizedContext`),
  });
}

function validatePromptProvenance(
  input: unknown,
  pathValue: string,
): StageAPromptProvenanceV2 {
  const record = snapshotRecord(input, pathValue, [
    "origin",
    "authorId",
    "agentRole",
    "model",
    "modelVersion",
    "reasoningEffort",
    "taskPromptDigest",
  ]);
  return Object.freeze({
    origin: requiredLiteral(record, "origin", `${pathValue}.origin`, "agent_synthetic"),
    authorId: evalIdField(record, "authorId", `${pathValue}.authorId`),
    agentRole: provenanceTokenField(record, "agentRole", `${pathValue}.agentRole`),
    model: provenanceTokenField(record, "model", `${pathValue}.model`),
    modelVersion: provenanceTokenField(record, "modelVersion", `${pathValue}.modelVersion`),
    reasoningEffort: requiredEnum(record, "reasoningEffort", `${pathValue}.reasoningEffort`, REASONING_EFFORTS),
    taskPromptDigest: validateDigest(required(record, "taskPromptDigest", `${pathValue}.taskPromptDigest`), `${pathValue}.taskPromptDigest`),
  });
}

function validateBundle(input: unknown, pathValue: string): StageABundleV2 {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion",
    "bundleId",
    "filterId",
    "cohort",
    "persona",
    "softQuery",
    "promptProvenance",
  ]);
  requiredLiteral(record, "schemaVersion", `${pathValue}.schemaVersion`, STAGE_A_BUNDLE_SCHEMA_VERSION);
  return Object.freeze({
    schemaVersion: STAGE_A_BUNDLE_SCHEMA_VERSION,
    bundleId: evalIdField(record, "bundleId", `${pathValue}.bundleId`),
    filterId: evalIdField(record, "filterId", `${pathValue}.filterId`),
    cohort: requiredEnum(record, "cohort", `${pathValue}.cohort`, COHORTS),
    persona: requiredEnum(record, "persona", `${pathValue}.persona`, PERSONAS),
    softQuery: validateSoftQuery(required(record, "softQuery", `${pathValue}.softQuery`), `${pathValue}.softQuery`),
    promptProvenance: validatePromptProvenance(required(record, "promptProvenance", `${pathValue}.promptProvenance`), `${pathValue}.promptProvenance`),
  });
}

function validatePair(input: unknown, pathValue: string): StageAPairV2 {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion",
    "pairId",
    "bundleId",
    "locale",
    "evidenceCondition",
    "ambiguity",
    "classifierSource",
    "contentIdentity",
    "annotations",
    "adjudication",
  ]);
  requiredLiteral(record, "schemaVersion", `${pathValue}.schemaVersion`, STAGE_A_PAIR_SCHEMA_VERSION);
  const annotations = snapshotArray(required(record, "annotations", `${pathValue}.annotations`), `${pathValue}.annotations`, 2, 2)
    .map((annotation, index) => validateAnnotation(annotation, `${pathValue}.annotations[${index}]`))
    .sort((left, right) => rawStringCompare(left.annotationId, right.annotationId)) as [StageAAnnotationV2, StageAAnnotationV2];
  if (annotations[0].annotationId === annotations[1].annotationId) {
    fail(`${pathValue}.annotations`, "unique_annotation_ids_required");
  }
  if (annotations[0].actorId === annotations[1].actorId) {
    fail(`${pathValue}.annotations`, "blind_distinct_annotators_required");
  }
  const classifierSource = validateClassifierSource(required(record, "classifierSource", `${pathValue}.classifierSource`), `${pathValue}.classifierSource`);
  const normalized = normalizeClassifierSource(classifierSource, `${pathValue}.classifierSource`);
  const contentIdentity = validateDigest(required(record, "contentIdentity", `${pathValue}.contentIdentity`), `${pathValue}.contentIdentity`);
  if (normalized.contentIdentity !== contentIdentity) {
    fail(`${pathValue}.contentIdentity`, "content_identity_mismatch");
  }
  const evidenceCondition = requiredEnum(record, "evidenceCondition", `${pathValue}.evidenceCondition`, EVIDENCE_CONDITIONS);
  const ambiguity = requiredBoolean(record, "ambiguity", `${pathValue}.ambiguity`);
  const adjudication = validateAdjudication(required(record, "adjudication", `${pathValue}.adjudication`), `${pathValue}.adjudication`);
  const needsAdjudication = annotations[0].label !== annotations[1].label || ambiguity || evidenceCondition === "policy_boundary";
  if (needsAdjudication !== Boolean(adjudication)) {
    fail(`${pathValue}.adjudication`, needsAdjudication ? "adjudication_required" : "unnecessary_adjudication_forbidden");
  }
  if (adjudication && annotations.some(({ actorId }) => actorId === adjudication.actorId)) {
    fail(`${pathValue}.adjudication`, "independent_adjudicator_required");
  }
  return Object.freeze({
    schemaVersion: STAGE_A_PAIR_SCHEMA_VERSION,
    pairId: evalIdField(record, "pairId", `${pathValue}.pairId`),
    bundleId: evalIdField(record, "bundleId", `${pathValue}.bundleId`),
    locale: requiredEnum(record, "locale", `${pathValue}.locale`, LOCALES),
    evidenceCondition,
    ambiguity,
    classifierSource,
    contentIdentity,
    annotations: Object.freeze(annotations),
    adjudication,
  });
}

function validateFinalCritic(input: unknown, pathValue: string): StageAFinalCriticV2 {
  const record = snapshotRecord(input, pathValue, ["reviewId", "actorId", "approved"]);
  return Object.freeze({
    reviewId: evalIdField(record, "reviewId", `${pathValue}.reviewId`),
    actorId: evalIdField(record, "actorId", `${pathValue}.actorId`),
    approved: requiredLiteral(record, "approved", `${pathValue}.approved`, true),
  });
}

function assertUnique(values: readonly string[], pathValue: string, rule: string): void {
  if (new Set(values).size !== values.length) fail(pathValue, rule);
}

export function validateStageAWip(input: unknown): StageAWipV2 {
  const record = snapshotRecord(input, "$", [
    "schemaVersion",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "softQueryNormalizerVersion",
    "calibrationDigest",
    "filters",
    "bundles",
    "pairs",
    "finalCritic",
  ]);
  requiredLiteral(record, "schemaVersion", "$.schemaVersion", STAGE_A_WIP_SCHEMA_VERSION);
  requiredLiteral(record, "classifierInputSchemaVersion", "$.classifierInputSchemaVersion", CLASSIFIER_INPUT_SCHEMA_VERSION);
  requiredLiteral(record, "classifierInputNormalizerVersion", "$.classifierInputNormalizerVersion", CLASSIFIER_INPUT_NORMALIZER_VERSION);
  requiredLiteral(record, "softQueryNormalizerVersion", "$.softQueryNormalizerVersion", AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION);
  const filters = snapshotArray(required(record, "filters", "$.filters"), "$.filters", STAGE_A_REQUIRED_FILTERS, STAGE_A_REQUIRED_FILTERS)
    .map((value, index) => validateFilter(value, `$.filters[${index}]`))
    .sort((left, right) => rawStringCompare(left.filterId, right.filterId));
  const bundles = snapshotArray(required(record, "bundles", "$.bundles"), "$.bundles", STAGE_A_REQUIRED_BUNDLES, STAGE_A_REQUIRED_BUNDLES)
    .map((value, index) => validateBundle(value, `$.bundles[${index}]`))
    .sort((left, right) => rawStringCompare(left.bundleId, right.bundleId));
  const pairs = snapshotArray(required(record, "pairs", "$.pairs"), "$.pairs", STAGE_A_REQUIRED_PAIRS, STAGE_A_REQUIRED_PAIRS)
    .map((value, index) => validatePair(value, `$.pairs[${index}]`))
    .sort((left, right) => rawStringCompare(left.pairId, right.pairId));
  const finalCritic = validateFinalCritic(required(record, "finalCritic", "$.finalCritic"), "$.finalCritic");

  assertUnique(filters.map(({ filterId }) => filterId), "$.filters", "unique_filter_ids_required");
  assertUnique(filters.map(({ sourceFilterDigest }) => sourceFilterDigest), "$.filters", "unique_source_filter_digests_required");
  assertUnique(bundles.map(({ bundleId }) => bundleId), "$.bundles", "unique_bundle_ids_required");
  assertUnique(bundles.map(({ softQuery }) => softQuery), "$.bundles", "unique_queries_required");
  assertUnique(pairs.map(({ pairId }) => pairId), "$.pairs", "unique_pair_ids_required");
  assertUnique(pairs.flatMap(({ annotations }) => annotations.map(({ annotationId }) => annotationId)), "$.pairs", "unique_annotation_ids_required");
  assertUnique(pairs.flatMap(({ adjudication }) => adjudication ? [adjudication.adjudicationId] : []), "$.pairs", "unique_adjudication_ids_required");
  const filterIds = new Set(filters.map(({ filterId }) => filterId));
  if (bundles.some(({ filterId }) => !filterIds.has(filterId))) fail("$.bundles", "known_filter_reference_required");
  const bundleById = new Map(bundles.map((bundle) => [bundle.bundleId, bundle]));
  if (pairs.some(({ bundleId }) => !bundleById.has(bundleId))) fail("$.pairs", "known_bundle_reference_required");
  const productionBundles = bundles.filter(({ cohort }) => cohort === "production_shaped");
  if (productionBundles.length !== STAGE_A_REQUIRED_PRODUCTION_SHAPED_BUNDLES) fail("$.bundles", "exact_cohort_split_required");
  if (bundles.length - productionBundles.length !== STAGE_A_REQUIRED_CHALLENGE_BUNDLES) fail("$.bundles", "exact_cohort_split_required");
  for (const bundle of bundles) {
    const bundlePairs = pairs.filter(({ bundleId }) => bundleId === bundle.bundleId);
    if (bundlePairs.length !== STAGE_A_REQUIRED_PAIRS_PER_BUNDLE) fail("$.pairs", "eight_pairs_per_bundle_required");
    assertUnique(bundlePairs.map(({ contentIdentity }) => contentIdentity), "$.pairs", "unique_candidates_per_bundle_required");
  }
  const filterUseCounts = filters.map(({ filterId }) => bundles.filter((bundle) => bundle.filterId === filterId).length);
  if (filterUseCounts.some((count) => count < 1 || count > 2) || filterUseCounts.filter((count) => count === 2).length !== 5) {
    fail("$.bundles", "five_extra_filter_uses_required");
  }
  const promptAuthors = new Set(bundles.map(({ promptProvenance }) => promptProvenance.authorId));
  const annotators = new Set(pairs.flatMap(({ annotations }) => annotations.map(({ actorId }) => actorId)));
  const adjudicators = new Set(pairs.flatMap(({ adjudication }) => adjudication ? [adjudication.actorId] : []));
  if (
    [...promptAuthors].some((actorId) => annotators.has(actorId) || adjudicators.has(actorId)) ||
    [...annotators].some((actorId) => adjudicators.has(actorId))
  ) {
    fail("$.pairs", "global_role_separation_required");
  }
  for (const pair of pairs) {
    const authorId = bundleById.get(pair.bundleId)!.promptProvenance.authorId;
    if (pair.annotations.some(({ actorId }) => actorId === authorId) || pair.adjudication?.actorId === authorId) {
      fail("$.pairs", "author_reviewer_separation_required");
    }
  }
  const contributors = new Set([...promptAuthors, ...annotators, ...adjudicators]);
  if (contributors.has(finalCritic.actorId)) fail("$.finalCritic.actorId", "independent_final_critic_required");

  return Object.freeze({
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: evalIdField(record, "datasetId", "$.datasetId"),
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationDigest: validateDigest(required(record, "calibrationDigest", "$.calibrationDigest"), "$.calibrationDigest"),
    filters: Object.freeze(filters),
    bundles: Object.freeze(bundles),
    pairs: Object.freeze(pairs),
    finalCritic,
  });
}

function canonicalValue(input: unknown): unknown {
  if (input === null || typeof input === "string" || typeof input === "boolean") return input;
  if (typeof input === "number") {
    if (!Number.isSafeInteger(input)) fail("$", "canonical_integer_required");
    return input;
  }
  if (Array.isArray(input)) return safeArray(input.map(canonicalValue));
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

function silverLabel(pair: StageAPairV2): StageALabel {
  return pair.adjudication?.label ?? pair.annotations[0].label;
}

export function buildStageASilverManifest(
  wipInput: unknown,
  expectedCalibrationDigest: string,
): StageASilverManifestV2 {
  validateDigest(expectedCalibrationDigest, "$expectedCalibrationDigest");
  const wip = validateStageAWip(wipInput);
  if (wip.calibrationDigest !== expectedCalibrationDigest) {
    fail("$.calibrationDigest", "calibration_digest_mismatch");
  }
  const sourceWipDigest = domainDigest(STAGE_A_WIP_SCHEMA_VERSION, wip);
  const pairs = wip.pairs.map((pair) => {
    const classifierInput = normalizeClassifierSource(pair.classifierSource, "$.pairs.classifierSource").payload;
    const adjudicated = pair.adjudication !== null;
    return Object.freeze({
      ...pair,
      classifierInput,
      silverLabel: silverLabel(pair),
      silverProvenance: Object.freeze({
        method: adjudicated ? "adjudication" as const : "agreement" as const,
        annotationIds: Object.freeze(pair.annotations.map(({ annotationId }) => annotationId)) as readonly [string, string],
        adjudicationId: pair.adjudication?.adjudicationId ?? null,
      }),
    });
  });
  return Object.freeze({
    schemaVersion: STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION,
    status: "agent_adjudicated_silver",
    datasetId: wip.datasetId,
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationDigest: wip.calibrationDigest,
    sourceWipDigest,
    filters: wip.filters,
    bundles: wip.bundles,
    pairs: Object.freeze(pairs),
    finalCritic: wip.finalCritic,
  });
}

export function freezeStageASilver(
  wipInput: unknown,
  expectedCalibrationDigest: string,
): StageASilverFreezeV2 {
  const manifest = buildStageASilverManifest(wipInput, expectedCalibrationDigest);
  return Object.freeze({
    schemaVersion: STAGE_A_SILVER_FREEZE_SCHEMA_VERSION,
    manifest,
    silverDigest: domainDigest(STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION, manifest),
  });
}

function basePairFromSilver(input: unknown, pathValue: string): unknown {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion", "pairId", "bundleId", "locale", "evidenceCondition", "ambiguity",
    "classifierSource", "contentIdentity", "annotations", "adjudication", "classifierInput",
    "silverLabel", "silverProvenance",
  ]);
  validateFrozenClassifierInput(required(record, "classifierInput", `${pathValue}.classifierInput`), `${pathValue}.classifierInput`);
  validateLabel(required(record, "silverLabel", `${pathValue}.silverLabel`), `${pathValue}.silverLabel`);
  const provenance = snapshotRecord(required(record, "silverProvenance", `${pathValue}.silverProvenance`), `${pathValue}.silverProvenance`, ["method", "annotationIds", "adjudicationId"]);
  requiredEnum(provenance, "method", `${pathValue}.silverProvenance.method`, ["agreement", "adjudication"]);
  snapshotArray(required(provenance, "annotationIds", `${pathValue}.silverProvenance.annotationIds`), `${pathValue}.silverProvenance.annotationIds`, 2, 2);
  const adjudicationId = required(provenance, "adjudicationId", `${pathValue}.silverProvenance.adjudicationId`);
  if (adjudicationId !== null) {
    if (typeof adjudicationId !== "string") fail(`${pathValue}.silverProvenance.adjudicationId`, "eval_id_required");
    validateEvalId(adjudicationId, `${pathValue}.silverProvenance.adjudicationId`);
  }
  return Object.fromEntries([
    "schemaVersion", "pairId", "bundleId", "locale", "evidenceCondition", "ambiguity",
    "classifierSource", "contentIdentity", "annotations", "adjudication",
  ].map((field) => [field, required(record, field, `${pathValue}.${field}`)]));
}

export function validateStageASilverFreeze(
  input: unknown,
  expectedSilverDigest: string,
  expectedCalibrationDigest: string,
): StageASilverFreezeV2 {
  validateDigest(expectedSilverDigest, "$expectedSilverDigest");
  validateDigest(expectedCalibrationDigest, "$expectedCalibrationDigest");
  const envelope = snapshotRecord(input, "$", ["schemaVersion", "manifest", "silverDigest"]);
  requiredLiteral(envelope, "schemaVersion", "$.schemaVersion", STAGE_A_SILVER_FREEZE_SCHEMA_VERSION);
  const manifestInput = required(envelope, "manifest", "$.manifest");
  const manifest = snapshotRecord(manifestInput, "$.manifest", [
    "schemaVersion", "status", "datasetId", "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion", "softQueryNormalizerVersion", "calibrationDigest",
    "sourceWipDigest", "filters", "bundles", "pairs", "finalCritic",
  ]);
  requiredLiteral(manifest, "schemaVersion", "$.manifest.schemaVersion", STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION);
  requiredLiteral(manifest, "status", "$.manifest.status", "agent_adjudicated_silver");
  const pairInputs = snapshotArray(required(manifest, "pairs", "$.manifest.pairs"), "$.manifest.pairs", STAGE_A_REQUIRED_PAIRS, STAGE_A_REQUIRED_PAIRS);
  const wip = {
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: required(manifest, "datasetId", "$.manifest.datasetId"),
    classifierInputSchemaVersion: required(manifest, "classifierInputSchemaVersion", "$.manifest.classifierInputSchemaVersion"),
    classifierInputNormalizerVersion: required(manifest, "classifierInputNormalizerVersion", "$.manifest.classifierInputNormalizerVersion"),
    softQueryNormalizerVersion: required(manifest, "softQueryNormalizerVersion", "$.manifest.softQueryNormalizerVersion"),
    calibrationDigest: required(manifest, "calibrationDigest", "$.manifest.calibrationDigest"),
    filters: required(manifest, "filters", "$.manifest.filters"),
    bundles: required(manifest, "bundles", "$.manifest.bundles"),
    pairs: pairInputs.map((pair, index) => basePairFromSilver(pair, `$.manifest.pairs[${index}]`)),
    finalCritic: required(manifest, "finalCritic", "$.manifest.finalCritic"),
  };
  const rebuilt = buildStageASilverManifest(wip, expectedCalibrationDigest);
  if (canonicalStageAJson(rebuilt) !== canonicalStageAJson(manifestInput)) fail("$.manifest", "derived_manifest_mismatch");
  const silverDigest = validateDigest(required(envelope, "silverDigest", "$.silverDigest"), "$.silverDigest");
  if (silverDigest !== expectedSilverDigest || silverDigest !== domainDigest(STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION, rebuilt)) {
    fail("$.silverDigest", "silver_digest_mismatch");
  }
  return Object.freeze({ schemaVersion: STAGE_A_SILVER_FREEZE_SCHEMA_VERSION, manifest: rebuilt, silverDigest });
}

export function validateStageAHumanAuditPolicy(input: unknown): StageAHumanAuditPolicyV2 {
  const record = snapshotRecord(input, "$policy", ["schemaVersion", "sourceSilverDigest", "auditPairIds"]);
  requiredLiteral(record, "schemaVersion", "$policy.schemaVersion", STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION);
  const auditPairIds = snapshotArray(required(record, "auditPairIds", "$policy.auditPairIds"), "$policy.auditPairIds", 1, STAGE_A_MAX_HUMAN_AUDIT_PAIRS)
    .map((value, index) => {
      if (typeof value !== "string") fail(`$policy.auditPairIds[${index}]`, "string_required");
      return validateEvalId(value, `$policy.auditPairIds[${index}]`);
    })
    .sort(rawStringCompare);
  assertUnique(auditPairIds, "$policy.auditPairIds", "unique_audit_pair_ids_required");
  return Object.freeze({
    schemaVersion: STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION,
    sourceSilverDigest: validateDigest(required(record, "sourceSilverDigest", "$policy.sourceSilverDigest"), "$policy.sourceSilverDigest"),
    auditPairIds: Object.freeze(auditPairIds),
  });
}

export function digestStageAHumanAuditPolicy(input: unknown): string {
  return domainDigest(STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION, validateStageAHumanAuditPolicy(input));
}

function validateHumanFeedback(input: unknown): StageAHumanFeedbackV2 {
  const record = snapshotRecord(input, "$feedback", [
    "schemaVersion", "feedbackId", "reviewerId", "sourceSilverDigest",
    "auditPolicyDigest", "approved", "decisions",
  ]);
  requiredLiteral(record, "schemaVersion", "$feedback.schemaVersion", STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION);
  const decisions = snapshotArray(required(record, "decisions", "$feedback.decisions"), "$feedback.decisions", 1, STAGE_A_MAX_HUMAN_AUDIT_PAIRS)
    .map((inputDecision, index) => {
      const decisionPath = `$feedback.decisions[${index}]`;
      const decision = snapshotRecord(inputDecision, decisionPath, ["pairId", "judgment"]);
      return Object.freeze({
        pairId: evalIdField(decision, "pairId", `${decisionPath}.pairId`),
        judgment: requiredEnum(decision, "judgment", `${decisionPath}.judgment`, ["accept", "reject", "unclear"]),
      });
    })
    .sort((left, right) => rawStringCompare(left.pairId, right.pairId));
  assertUnique(decisions.map(({ pairId }) => pairId), "$feedback.decisions", "unique_feedback_pair_ids_required");
  return Object.freeze({
    schemaVersion: STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION,
    feedbackId: evalIdField(record, "feedbackId", "$feedback.feedbackId"),
    reviewerId: evalIdField(record, "reviewerId", "$feedback.reviewerId"),
    sourceSilverDigest: validateDigest(required(record, "sourceSilverDigest", "$feedback.sourceSilverDigest"), "$feedback.sourceSilverDigest"),
    auditPolicyDigest: validateDigest(required(record, "auditPolicyDigest", "$feedback.auditPolicyDigest"), "$feedback.auditPolicyDigest"),
    approved: requiredBoolean(record, "approved", "$feedback.approved"),
    decisions: Object.freeze(decisions),
  });
}

export function promoteStageAGold(
  silverInput: unknown,
  expectedSilverDigest: string,
  expectedCalibrationDigest: string,
  policyInput: unknown,
  expectedPolicyDigest: string,
  feedbackInput: unknown,
): StageAGoldFreezeV2 {
  const silver = validateStageASilverFreeze(silverInput, expectedSilverDigest, expectedCalibrationDigest);
  const policy = validateStageAHumanAuditPolicy(policyInput);
  validateDigest(expectedPolicyDigest, "$expectedPolicyDigest");
  if (policy.sourceSilverDigest !== silver.silverDigest || digestStageAHumanAuditPolicy(policy) !== expectedPolicyDigest) {
    fail("$policy", "audit_policy_pin_mismatch");
  }
  const pairIds = new Set(silver.manifest.pairs.map(({ pairId }) => pairId));
  if (policy.auditPairIds.some((pairId) => !pairIds.has(pairId))) fail("$policy.auditPairIds", "known_pair_ids_required");
  const feedback = validateHumanFeedback(feedbackInput);
  if (feedback.sourceSilverDigest !== silver.silverDigest || feedback.auditPolicyDigest !== expectedPolicyDigest) {
    fail("$feedback", "feedback_pin_mismatch");
  }
  if (!feedback.approved) fail("$feedback.approved", "explicit_human_approval_required");
  if (feedback.decisions.some(({ judgment }) => judgment === "unclear")) {
    fail("$feedback.decisions", "resolved_human_feedback_required");
  }
  if (canonicalStageAJson(feedback.decisions.map(({ pairId }) => pairId)) !== canonicalStageAJson(policy.auditPairIds)) {
    fail("$feedback.decisions", "complete_precommitted_audit_required");
  }
  const priorActors = new Set<string>([
    silver.manifest.finalCritic.actorId,
    ...silver.manifest.bundles.map(({ promptProvenance }) => promptProvenance.authorId),
    ...silver.manifest.pairs.flatMap(({ annotations, adjudication }) => [
      ...annotations.map(({ actorId }) => actorId),
      ...(adjudication ? [adjudication.actorId] : []),
    ]),
  ]);
  if (priorActors.has(feedback.reviewerId)) fail("$feedback.reviewerId", "independent_human_reviewer_required");
  const judgments = new Map(feedback.decisions.map(({ pairId, judgment }) => [pairId, judgment as StageALabel]));
  const pairs = silver.manifest.pairs.map((pair) => {
    const humanJudgment = judgments.get(pair.pairId);
    const corrected = humanJudgment !== undefined && humanJudgment !== pair.silverLabel;
    return Object.freeze({
      ...pair,
      goldLabel: humanJudgment ?? pair.silverLabel,
      goldProvenance: Object.freeze({
        source: corrected ? "human_correction" as const : "silver" as const,
        sourcePairId: pair.pairId,
        humanFeedbackId: humanJudgment === undefined ? null : feedback.feedbackId,
      }),
    });
  });
  const manifest: StageAGoldManifestV2 = Object.freeze({
    schemaVersion: STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION,
    status: "human_audited_gold",
    sourceSilverDigest: silver.silverDigest,
    auditPolicyDigest: expectedPolicyDigest,
    humanFeedbackDigest: domainDigest(STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION, feedback),
    humanFeedbackId: feedback.feedbackId,
    datasetId: silver.manifest.datasetId,
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationDigest: silver.manifest.calibrationDigest,
    filters: silver.manifest.filters,
    bundles: silver.manifest.bundles,
    pairs: Object.freeze(pairs),
    finalCritic: silver.manifest.finalCritic,
  });
  return Object.freeze({
    schemaVersion: STAGE_A_GOLD_FREEZE_SCHEMA_VERSION,
    manifest,
    goldDigest: domainDigest(STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION, manifest),
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

export async function readStageAWipFile(relativePath: string): Promise<StageAWipV2> {
  return validateStageAWip(await readPrivateJson(relativePath));
}

export async function writeStageASilverFreezeFile(
  relativePath: string,
  wipInput: unknown,
  expectedCalibrationDigest: string,
): Promise<Readonly<{ silverDigest: string }>> {
  const frozen = freezeStageASilver(wipInput, expectedCalibrationDigest);
  await publishPrivateFile(relativePath, canonicalStageAJson(frozen));
  return Object.freeze({ silverDigest: frozen.silverDigest });
}

export async function writeStageAGoldFreezeFile(
  relativePath: string,
  silverInput: unknown,
  expectedSilverDigest: string,
  expectedCalibrationDigest: string,
  policyInput: unknown,
  expectedPolicyDigest: string,
  feedbackInput: unknown,
): Promise<Readonly<{ goldDigest: string }>> {
  const frozen = promoteStageAGold(
    silverInput,
    expectedSilverDigest,
    expectedCalibrationDigest,
    policyInput,
    expectedPolicyDigest,
    feedbackInput,
  );
  await publishPrivateFile(relativePath, canonicalStageAJson(frozen));
  return Object.freeze({ goldDigest: frozen.goldDigest });
}

function deepFreeze<T>(input: T): T {
  if (typeof input !== "object" || input === null || Object.isFrozen(input)) return input;
  for (const value of Object.values(input)) deepFreeze(value);
  return Object.freeze(input);
}

function validateSilverProvenance(
  input: unknown,
  pair: StageAPairV2,
  pathValue: string,
): StageASilverProvenanceV2 {
  const record = snapshotRecord(input, pathValue, ["method", "annotationIds", "adjudicationId"]);
  const method = requiredEnum(record, "method", `${pathValue}.method`, ["agreement", "adjudication"]);
  const annotationIds = snapshotArray(required(record, "annotationIds", `${pathValue}.annotationIds`), `${pathValue}.annotationIds`, 2, 2).map((value, index) => {
    if (typeof value !== "string") fail(`${pathValue}.annotationIds[${index}]`, "string_required");
    return validateEvalId(value, `${pathValue}.annotationIds[${index}]`);
  }).sort(rawStringCompare) as [string, string];
  if (canonicalStageAJson(annotationIds) !== canonicalStageAJson(pair.annotations.map(({ annotationId }) => annotationId))) {
    fail(`${pathValue}.annotationIds`, "annotation_provenance_mismatch");
  }
  const rawAdjudicationId = required(record, "adjudicationId", `${pathValue}.adjudicationId`);
  const adjudicationId = rawAdjudicationId === null
    ? null
    : typeof rawAdjudicationId === "string"
      ? validateEvalId(rawAdjudicationId, `${pathValue}.adjudicationId`)
      : fail(`${pathValue}.adjudicationId`, "eval_id_required");
  const expectedMethod = pair.adjudication ? "adjudication" : "agreement";
  if (method !== expectedMethod || adjudicationId !== (pair.adjudication?.adjudicationId ?? null)) {
    fail(pathValue, "silver_provenance_mismatch");
  }
  return Object.freeze({ method, annotationIds: Object.freeze(annotationIds), adjudicationId });
}

function validateGoldPair(
  input: unknown,
  pathValue: string,
  humanFeedbackId: string,
): StageAGoldPairV2 {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion", "pairId", "bundleId", "locale", "evidenceCondition", "ambiguity",
    "classifierSource", "contentIdentity", "annotations", "adjudication", "classifierInput",
    "silverLabel", "silverProvenance", "goldLabel", "goldProvenance",
  ]);
  const baseInput = Object.fromEntries([
    "schemaVersion", "pairId", "bundleId", "locale", "evidenceCondition", "ambiguity",
    "classifierSource", "contentIdentity", "annotations", "adjudication",
  ].map((field) => [field, required(record, field, `${pathValue}.${field}`)]));
  const pair = validatePair(baseInput, pathValue);
  const frozenInputValue = required(record, "classifierInput", `${pathValue}.classifierInput`);
  validateFrozenClassifierInput(frozenInputValue, `${pathValue}.classifierInput`);
  const normalizedInput = normalizeClassifierSource(pair.classifierSource, `${pathValue}.classifierSource`).payload;
  if (canonicalStageAJson(frozenInputValue) !== canonicalStageAJson(normalizedInput)) {
    fail(`${pathValue}.classifierInput`, "classifier_input_mismatch");
  }
  const frozenInput = normalizedInput;
  const expectedSilverLabel = silverLabel(pair);
  const embeddedSilverLabel = validateLabel(required(record, "silverLabel", `${pathValue}.silverLabel`), `${pathValue}.silverLabel`);
  if (embeddedSilverLabel !== expectedSilverLabel) fail(`${pathValue}.silverLabel`, "silver_label_mismatch");
  const silverProvenance = validateSilverProvenance(required(record, "silverProvenance", `${pathValue}.silverProvenance`), pair, `${pathValue}.silverProvenance`);
  const goldLabel = validateLabel(required(record, "goldLabel", `${pathValue}.goldLabel`), `${pathValue}.goldLabel`);
  const provenanceRecord = snapshotRecord(required(record, "goldProvenance", `${pathValue}.goldProvenance`), `${pathValue}.goldProvenance`, ["source", "sourcePairId", "humanFeedbackId"]);
  const source = requiredEnum(provenanceRecord, "source", `${pathValue}.goldProvenance.source`, ["silver", "human_correction"]);
  const sourcePairId = evalIdField(provenanceRecord, "sourcePairId", `${pathValue}.goldProvenance.sourcePairId`);
  const rawFeedbackId = required(provenanceRecord, "humanFeedbackId", `${pathValue}.goldProvenance.humanFeedbackId`);
  const rowFeedbackId = rawFeedbackId === null
    ? null
    : typeof rawFeedbackId === "string"
      ? validateEvalId(rawFeedbackId, `${pathValue}.goldProvenance.humanFeedbackId`)
      : fail(`${pathValue}.goldProvenance.humanFeedbackId`, "eval_id_required");
  if (sourcePairId !== pair.pairId || (rowFeedbackId !== null && rowFeedbackId !== humanFeedbackId)) {
    fail(`${pathValue}.goldProvenance`, "gold_provenance_mismatch");
  }
  const corrected = goldLabel !== embeddedSilverLabel;
  if ((source === "human_correction") !== corrected || (corrected && rowFeedbackId === null)) {
    fail(`${pathValue}.goldProvenance`, "gold_provenance_mismatch");
  }
  return Object.freeze({
    ...pair,
    classifierInput: frozenInput,
    silverLabel: embeddedSilverLabel,
    silverProvenance,
    goldLabel,
    goldProvenance: Object.freeze({ source, sourcePairId, humanFeedbackId: rowFeedbackId }),
  });
}

function validateGoldFreeze(
  input: unknown,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationDigest: string,
): StageAGoldFreezeV2 {
  validateDigest(expectedGoldDigest, "$expectedGoldDigest");
  validateDigest(expectedSilverDigest, "$expectedSilverDigest");
  validateDigest(expectedAuditPolicyDigest, "$expectedAuditPolicyDigest");
  validateDigest(expectedCalibrationDigest, "$expectedCalibrationDigest");
  const envelope = snapshotRecord(input, "$", ["schemaVersion", "manifest", "goldDigest"]);
  requiredLiteral(envelope, "schemaVersion", "$.schemaVersion", STAGE_A_GOLD_FREEZE_SCHEMA_VERSION);
  const manifestInput = required(envelope, "manifest", "$.manifest");
  const record = snapshotRecord(manifestInput, "$.manifest", [
    "schemaVersion", "status", "sourceSilverDigest", "auditPolicyDigest",
    "humanFeedbackDigest", "humanFeedbackId", "datasetId", "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion", "softQueryNormalizerVersion", "calibrationDigest",
    "filters", "bundles", "pairs", "finalCritic",
  ]);
  requiredLiteral(record, "schemaVersion", "$.manifest.schemaVersion", STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION);
  requiredLiteral(record, "status", "$.manifest.status", "human_audited_gold");
  requiredLiteral(record, "classifierInputSchemaVersion", "$.manifest.classifierInputSchemaVersion", CLASSIFIER_INPUT_SCHEMA_VERSION);
  requiredLiteral(record, "classifierInputNormalizerVersion", "$.manifest.classifierInputNormalizerVersion", CLASSIFIER_INPUT_NORMALIZER_VERSION);
  requiredLiteral(record, "softQueryNormalizerVersion", "$.manifest.softQueryNormalizerVersion", AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION);
  const sourceSilverDigest = validateDigest(required(record, "sourceSilverDigest", "$.manifest.sourceSilverDigest"), "$.manifest.sourceSilverDigest");
  const auditPolicyDigest = validateDigest(required(record, "auditPolicyDigest", "$.manifest.auditPolicyDigest"), "$.manifest.auditPolicyDigest");
  const calibrationDigest = validateDigest(required(record, "calibrationDigest", "$.manifest.calibrationDigest"), "$.manifest.calibrationDigest");
  if (sourceSilverDigest !== expectedSilverDigest || auditPolicyDigest !== expectedAuditPolicyDigest || calibrationDigest !== expectedCalibrationDigest) {
    fail("$.manifest", "external_pin_mismatch");
  }
  const humanFeedbackId = evalIdField(record, "humanFeedbackId", "$.manifest.humanFeedbackId");
  const filtersInput = required(record, "filters", "$.manifest.filters");
  const bundlesInput = required(record, "bundles", "$.manifest.bundles");
  const pairInputs = snapshotArray(required(record, "pairs", "$.manifest.pairs"), "$.manifest.pairs", STAGE_A_REQUIRED_PAIRS, STAGE_A_REQUIRED_PAIRS);
  const pairs = pairInputs.map((pair, index) => validateGoldPair(pair, `$.manifest.pairs[${index}]`, humanFeedbackId));
  const wip = validateStageAWip({
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: required(record, "datasetId", "$.manifest.datasetId"),
    classifierInputSchemaVersion: required(record, "classifierInputSchemaVersion", "$.manifest.classifierInputSchemaVersion"),
    classifierInputNormalizerVersion: required(record, "classifierInputNormalizerVersion", "$.manifest.classifierInputNormalizerVersion"),
    softQueryNormalizerVersion: required(record, "softQueryNormalizerVersion", "$.manifest.softQueryNormalizerVersion"),
    calibrationDigest,
    filters: filtersInput,
    bundles: bundlesInput,
    pairs: pairs.map(({ classifierInput: _classifierInput, silverLabel: _silverLabel, silverProvenance: _silverProvenance, goldLabel: _goldLabel, goldProvenance: _goldProvenance, ...pair }) => pair),
    finalCritic: required(record, "finalCritic", "$.manifest.finalCritic"),
  });
  const auditedCount = pairs.filter(({ goldProvenance }) => goldProvenance.humanFeedbackId !== null).length;
  if (auditedCount < 1 || auditedCount > STAGE_A_MAX_HUMAN_AUDIT_PAIRS) fail("$.manifest.pairs", "bounded_human_audit_required");
  const manifest: StageAGoldManifestV2 = Object.freeze({
    schemaVersion: STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION,
    status: "human_audited_gold",
    sourceSilverDigest,
    auditPolicyDigest,
    humanFeedbackDigest: validateDigest(required(record, "humanFeedbackDigest", "$.manifest.humanFeedbackDigest"), "$.manifest.humanFeedbackDigest"),
    humanFeedbackId,
    datasetId: wip.datasetId,
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationDigest,
    filters: wip.filters,
    bundles: wip.bundles,
    pairs: Object.freeze(pairs.sort((left, right) => rawStringCompare(left.pairId, right.pairId))),
    finalCritic: wip.finalCritic,
  });
  if (canonicalStageAJson(manifest) !== canonicalStageAJson(manifestInput)) fail("$.manifest", "canonical_manifest_required");
  const goldDigest = validateDigest(required(envelope, "goldDigest", "$.goldDigest"), "$.goldDigest");
  if (goldDigest !== expectedGoldDigest || goldDigest !== domainDigest(STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION, manifest)) {
    fail("$.goldDigest", "gold_digest_mismatch");
  }
  return Object.freeze({ schemaVersion: STAGE_A_GOLD_FREEZE_SCHEMA_VERSION, manifest, goldDigest });
}

async function readValidatedGoldFreeze(
  relativePath: string,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationDigest: string,
): Promise<StageAGoldFreezeV2> {
  return validateGoldFreeze(
    await readPrivateJson(relativePath),
    expectedGoldDigest,
    expectedSilverDigest,
    expectedAuditPolicyDigest,
    expectedCalibrationDigest,
  );
}

export async function loadStageATargetInputs(
  relativePath: string,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationDigest: string,
): Promise<readonly StageATargetInputV2[]> {
  const frozen = await readValidatedGoldFreeze(relativePath, expectedGoldDigest, expectedSilverDigest, expectedAuditPolicyDigest, expectedCalibrationDigest);
  const queryByBundle = new Map(frozen.manifest.bundles.map(({ bundleId, softQuery }) => [bundleId, softQuery]));
  return deepFreeze({
    values: safeArray(frozen.manifest.pairs.map(({ pairId, bundleId, classifierInput }) => frozenNullPrototypeRecord({ pairId, query: queryByBundle.get(bundleId)!, classifierInput }))),
  }).values;
}

export async function loadStageAScoringLabels(
  relativePath: string,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationDigest: string,
): Promise<readonly StageAScoringLabelV2[]> {
  const frozen = await readValidatedGoldFreeze(relativePath, expectedGoldDigest, expectedSilverDigest, expectedAuditPolicyDigest, expectedCalibrationDigest);
  return deepFreeze(safeArray(frozen.manifest.pairs.map(({ pairId, goldLabel }) => frozenNullPrototypeRecord({ pairId, label: goldLabel }))));
}

export async function reportStageAGoldFreeze(
  relativePath: string,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationDigest: string,
): Promise<StageAReportV2> {
  const frozen = await readValidatedGoldFreeze(relativePath, expectedGoldDigest, expectedSilverDigest, expectedAuditPolicyDigest, expectedCalibrationDigest);
  const cohortByBundle = new Map(frozen.manifest.bundles.map(({ bundleId, cohort }) => [bundleId, cohort]));
  const cohorts = Object.fromEntries(COHORTS.map((cohort) => {
    const pairs = frozen.manifest.pairs.filter(({ bundleId }) => cohortByBundle.get(bundleId) === cohort);
    const accept = pairs.filter(({ goldLabel }) => goldLabel === "accept").length;
    const reject = pairs.length - accept;
    const labels = accept < STAGE_A_REPORT_MIN_CELL_SIZE || reject < STAGE_A_REPORT_MIN_CELL_SIZE
      ? Object.freeze({ suppressed: true as const })
      : Object.freeze({ suppressed: false as const, accept, reject });
    return [cohort, Object.freeze({ totalPairs: pairs.length, labels })];
  })) as Record<StageACohort, { totalPairs: number; labels: { suppressed: true } | { suppressed: false; accept: number; reject: number } }>;
  return deepFreeze({ schemaVersion: "ai-filter-stage-a-report-v2", cohorts });
}

export async function readStageAHumanAuditPolicyFile(
  relativePath: string,
): Promise<StageAHumanAuditPolicyV2> {
  return validateStageAHumanAuditPolicy(await readPrivateJson(relativePath));
}
