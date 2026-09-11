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
import { isProxy } from "node:util/types";
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
export const STAGE_A_PRE_ANNOTATION_SCHEMA_VERSION =
  "ai-filter-stage-a-pre-annotation-v2" as const;
export const STAGE_A_CALIBRATION_RESULT_SCHEMA_VERSION =
  "ai-filter-stage-a-calibration-result-v2" as const;
export const STAGE_A_EXTRACTION_MANIFEST_SCHEMA_VERSION =
  "ai-filter-stage-a-extraction-manifest-v2" as const;
export const STAGE_A_PROMPT_REVIEW_FEEDBACK_SCHEMA_VERSION =
  "ai-filter-stage-a-prompt-review-feedback-v2" as const;
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
export const STAGE_A_HUMAN_AUDIT_PAIRS = 32;
export const STAGE_A_REPORT_MIN_CELL_SIZE = 10;
export const STAGE_A_CALIBRATION_MIN_EXAMPLES = 24;
export const STAGE_A_CALIBRATION_MAX_EXAMPLES = 32;
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
const AGENT_ROLES = ["prompt_author", "annotator", "adjudicator", "final_critic"] as const;
const CALIBRATION_SUITE_KINDS = [
  "same_eight_disposable_feeds",
  "resolved_human_ground_truth",
  "seeded_conflicts",
  "seeded_defects",
] as const;
const CALIBRATION_SUITE_BY_ROLE: Readonly<Record<StageAAgentRole, StageACalibrationSuiteKind>> = Object.freeze({
  prompt_author: "same_eight_disposable_feeds",
  annotator: "resolved_human_ground_truth",
  adjudicator: "seeded_conflicts",
  final_critic: "seeded_defects",
});
const STAGE_A_COMPILER_SOURCE_PATH =
  "apps/web/src/lib/search/watchlist-candidate-query.ts" as const;
const STAGE_A_COMPILER_EXPORT = "buildWatchlistCandidateSearchParams" as const;
const STAGE_A_READER_SOURCE_PATH =
  "apps/web/src/lib/services/watchlist-matcher.ts" as const;
const STAGE_A_READER_EXPORT = "readWatchlistCandidates" as const;
const STAGE_A_CLASSIFIER_NORMALIZER_SOURCE_PATH =
  "apps/web/src/lib/ai-filter/classifier-input.ts" as const;
const STAGE_A_CLASSIFIER_NORMALIZER_EXPORT = "normalizeClassifierInputV1" as const;
const STAGE_A_SOFT_QUERY_NORMALIZER_SOURCE_PATH =
  "apps/web/src/lib/ai-filter/contract.ts" as const;
const STAGE_A_SOFT_QUERY_NORMALIZER_EXPORT = "normalizeAiFilterSoftQueryV1" as const;
const RATIONALE_CODES = [
  "direct_evidence",
  "missing_evidence",
  "contradiction",
  "policy_interpretation",
  "prompt_injection_ignored",
] as const;
const EVIDENCE_REFS = ["title", "company_name", "description_text", "absence_in_snapshot"] as const;
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
export type StageAAgentRole = (typeof AGENT_ROLES)[number];
export type StageARationaleCode = (typeof RATIONALE_CODES)[number];
export type StageAEvidenceRef = (typeof EVIDENCE_REFS)[number];
export type StageAAuditStratum =
  | "agreement_clear"
  | "adjudicated_disagreement"
  | "adjudicated_ambiguous_policy";

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
  readonly generalizedContext: StageAGeneralizedFilterContextV2;
};

export type StageAPromptProvenanceV2 = {
  readonly origin: "agent_synthetic";
  readonly authorId: string;
  readonly configId: string;
};

export type StageABundleV2 = {
  readonly schemaVersion: typeof STAGE_A_BUNDLE_SCHEMA_VERSION;
  readonly bundleId: string;
  readonly filterId: string;
  readonly cohort: StageACohort;
  readonly persona: StageAPersona;
  readonly promptLocale: StageALocale;
  readonly softQuery: string;
  readonly promptProvenance: StageAPromptProvenanceV2;
};

export type StageAAnnotationV2 = {
  readonly annotationId: string;
  readonly actorId: string;
  readonly configId: string;
  readonly label: StageALabel;
  readonly ambiguity: boolean;
  readonly rationaleCode: StageARationaleCode;
  readonly evidenceRefs: readonly StageAEvidenceRef[];
};

export type StageAAdjudicationV2 = {
  readonly adjudicationId: string;
  readonly actorId: string;
  readonly configId: string;
  readonly annotationIds: readonly [string, string];
  readonly label: StageALabel;
  readonly ambiguity: boolean;
  readonly rationaleCode: StageARationaleCode;
};

export type StageAPairV2 = {
  readonly schemaVersion: typeof STAGE_A_PAIR_SCHEMA_VERSION;
  readonly pairId: string;
  readonly bundleId: string;
  readonly position: number;
  readonly sourceRank: number;
  readonly postingFirstSeenAt: string;
  readonly sourceSnapshotIdentity: string;
  readonly locale: StageALocale;
  readonly evidenceCondition: StageAEvidenceCondition;
  readonly classifierSource: ClassifierInputSource;
  readonly contentIdentity: string;
  readonly annotations: readonly [StageAAnnotationV2, StageAAnnotationV2];
  readonly adjudication: StageAAdjudicationV2 | null;
};

export type StageAFinalCriticV2 = {
  readonly reviewId: string;
  readonly actorId: string;
  readonly configId: string;
  readonly reviewedWipDigest: string;
  readonly approved: true;
};

export type StageAAgentConfigV2 = {
  readonly configId: string;
  readonly role: StageAAgentRole;
  readonly model: string;
  readonly modelVersion: string;
  readonly reasoningEffort: (typeof REASONING_EFFORTS)[number];
  readonly taskPromptDigest: string;
};

export type StageACalibrationSuiteKind =
  | "same_eight_disposable_feeds"
  | "resolved_human_ground_truth"
  | "seeded_conflicts"
  | "seeded_defects";

export type StageACalibrationTrialV2 = Readonly<{
  trialId: string;
  configId: string;
  role: StageAAgentRole;
  suiteKind: StageACalibrationSuiteKind;
  suiteInputDigest: string;
  outputDigest: string;
  sampleCount: number;
  blindedScore: number;
  spotCheck: Readonly<{
    disposition: "pass" | "fail";
    failureCodes: readonly string[];
  }>;
}>;

export type StageASelectedConfigV2 = Readonly<{
  role: StageAAgentRole;
  configId: string;
  selectionDisposition: "approved";
}>;

export type StageACalibrationArtifactV2 = Readonly<{
  schemaVersion: "ai-filter-stage-a-calibration-v2";
  calibrationId: string;
  examples: readonly Readonly<{
    calibrationExampleId: string;
    softQuery: string;
    classifierSource: ClassifierInputSource;
    contentIdentity: string;
  }>[];
}>;

export type StageAValidatedCalibrationArtifactV2 = Readonly<{
  calibrationId: string;
  examples: readonly Readonly<{
    calibrationExampleId: string;
    softQuery: string;
    classifierInput: ClassifierInputV1;
  }>[];
}>;

export type StageACalibrationResultV2 = {
  readonly schemaVersion: typeof STAGE_A_CALIBRATION_RESULT_SCHEMA_VERSION;
  readonly calibrationId: string;
  readonly calibrationInputDigest: string;
  readonly humanReview: Readonly<{
    reviewId: string;
    reviewerId: string;
    approved: true;
    decisions: readonly Readonly<{
      calibrationExampleId: string;
      judgment: StageALabel | "unclear";
    }>[];
  }>;
  readonly candidateConfigs: readonly StageAAgentConfigV2[];
  readonly trials: readonly StageACalibrationTrialV2[];
  readonly selectedConfigs: readonly StageASelectedConfigV2[];
};

type StageASourcePinV2 = Readonly<{
  sourcePath: string;
  exportName: string;
  sourceDigest: string;
}>;

type StageANormalizerPinV2 = StageASourcePinV2 & Readonly<{
  version: string | number;
  fallbackPolicyDigest: string;
  truncationPolicyDigest: string;
}>;

export type StageAExtractionManifestV2 = {
  readonly schemaVersion: typeof STAGE_A_EXTRACTION_MANIFEST_SCHEMA_VERSION;
  readonly repositoryCommit: string;
  readonly af1ContractVersion: 1;
  readonly typesense: Readonly<{
    collectionAlias: "job_posting";
    resolvedCollection: string;
    snapshotDigest: string;
  }>;
  readonly compiler: StageASourcePinV2;
  readonly reader: StageASourcePinV2;
  readonly dependencyLockDigest: string;
  readonly compiledQueries: readonly Readonly<{
    filterId: string;
    fingerprint: Readonly<{
      scheme: "hmac-sha256-v1";
      value: string;
    }>;
  }>[];
  readonly query: Readonly<{
    templateDigest: string;
    order: "first_seen_at_desc_candidate_id_asc";
    pageSize: 8;
    requestedStrictLowerBound: string;
    effectiveWindowStart: string;
    cutoff: string;
    requestedBoundary: "(requestedStrictLowerBound,cutoff)";
    effectiveBoundary: "[effectiveWindowStart,cutoff)";
    productionSelection: "first_eight";
    challengeSelection: "frozen_source_rank";
  }>;
  readonly classifierNormalizer: StageANormalizerPinV2;
  readonly softQueryNormalizer: StageANormalizerPinV2;
};

export type StageAReviewPlanV2 = {
  readonly promptReviewSeed: string;
  readonly auditSeed: string;
  readonly auditRule: "bounded-16-8-8-v2";
  readonly auditSize: typeof STAGE_A_HUMAN_AUDIT_PAIRS;
};

export type StageAPreAnnotationPairV2 = Omit<StageAPairV2, "annotations" | "adjudication">;

export type StageAPreAnnotationV2 = {
  readonly schemaVersion: typeof STAGE_A_PRE_ANNOTATION_SCHEMA_VERSION;
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly softQueryNormalizerVersion: typeof AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION;
  readonly calibrationResultDigest: string;
  readonly extractionManifest: StageAExtractionManifestV2;
  readonly extractionManifestDigest: string;
  readonly reviewPlan: StageAReviewPlanV2;
  readonly filters: readonly StageAFilterV2[];
  readonly bundles: readonly StageABundleV2[];
  readonly pairs: readonly StageAPreAnnotationPairV2[];
};

export type StageAPromptReviewFeedbackV2 = {
  readonly schemaVersion: typeof STAGE_A_PROMPT_REVIEW_FEEDBACK_SCHEMA_VERSION;
  readonly reviewId: string;
  readonly reviewerId: string;
  readonly preAnnotationDigest: string;
  readonly approved: boolean;
  readonly decisions: readonly Readonly<{
    bundleId: string;
    decision: "keep" | "revise" | "reject";
  }>[];
};

export type StageAWipV2 = {
  readonly schemaVersion: typeof STAGE_A_WIP_SCHEMA_VERSION;
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly softQueryNormalizerVersion: typeof AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION;
  readonly calibrationResultDigest: string;
  readonly preAnnotationDigest: string;
  readonly promptReviewFeedbackDigest: string;
  readonly filters: readonly StageAFilterV2[];
  readonly bundles: readonly StageABundleV2[];
  readonly pairs: readonly StageAPairV2[];
  readonly finalCritic: StageAFinalCriticV2;
};

export type StageAWipReviewPayloadV2 = Omit<StageAWipV2, "finalCritic">;

export type StageASilverProvenanceV2 = {
  readonly method: "agreement" | "adjudication";
  readonly annotationIds: readonly [string, string];
  readonly adjudicationId: string | null;
};

export type StageASilverPairV2 = StageAPairV2 & {
  readonly classifierInput: ClassifierInputV1;
  readonly silverLabel: StageALabel;
  readonly finalAmbiguity: boolean;
  readonly silverProvenance: StageASilverProvenanceV2;
};

export type StageASilverManifestV2 = {
  readonly schemaVersion: typeof STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION;
  readonly status: "agent_adjudicated_silver";
  readonly datasetId: string;
  readonly classifierInputSchemaVersion: typeof CLASSIFIER_INPUT_SCHEMA_VERSION;
  readonly classifierInputNormalizerVersion: typeof CLASSIFIER_INPUT_NORMALIZER_VERSION;
  readonly softQueryNormalizerVersion: typeof AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION;
  readonly calibrationResultDigest: string;
  readonly preAnnotationDigest: string;
  readonly promptReviewFeedbackDigest: string;
  readonly sourceWipDigest: string;
  readonly extractionManifest: StageAExtractionManifestV2;
  readonly extractionManifestDigest: string;
  readonly reviewPlan: StageAReviewPlanV2;
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
  readonly rule: StageAReviewPlanV2["auditRule"];
  readonly seed: string;
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
  readonly source: "agent_agreed" | "agent_adjudicated" | "human_reviewed";
  readonly sourcePairId: string;
  readonly humanFeedbackId: string | null;
  readonly corrected: boolean;
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
  readonly calibrationResultDigest: string;
  readonly preAnnotationDigest: string;
  readonly promptReviewFeedbackDigest: string;
  readonly extractionManifest: StageAExtractionManifestV2;
  readonly extractionManifestDigest: string;
  readonly reviewPlan: StageAReviewPlanV2;
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
  const record = snapshotRecord(input, pathValue, [
    "annotationId", "actorId", "configId", "label", "ambiguity", "rationaleCode", "evidenceRefs",
  ]);
  const evidenceRefs = snapshotArray(required(record, "evidenceRefs", `${pathValue}.evidenceRefs`), `${pathValue}.evidenceRefs`, 1, 4)
    .map((value, index) => {
      if (typeof value !== "string" || !EVIDENCE_REFS.includes(value as StageAEvidenceRef)) fail(`${pathValue}.evidenceRefs[${index}]`, "evidence_ref_required");
      return value as StageAEvidenceRef;
    })
    .sort(rawStringCompare);
  assertUnique(evidenceRefs, `${pathValue}.evidenceRefs`, "unique_evidence_refs_required");
  return Object.freeze({
    annotationId: evalIdField(record, "annotationId", `${pathValue}.annotationId`),
    actorId: evalIdField(record, "actorId", `${pathValue}.actorId`),
    configId: evalIdField(record, "configId", `${pathValue}.configId`),
    label: validateLabel(required(record, "label", `${pathValue}.label`), `${pathValue}.label`),
    ambiguity: requiredBoolean(record, "ambiguity", `${pathValue}.ambiguity`),
    rationaleCode: requiredEnum(record, "rationaleCode", `${pathValue}.rationaleCode`, RATIONALE_CODES),
    evidenceRefs: Object.freeze(evidenceRefs),
  });
}

function validateAdjudication(input: unknown, pathValue: string): StageAAdjudicationV2 | null {
  if (input === null) return null;
  const record = snapshotRecord(input, pathValue, [
    "adjudicationId", "actorId", "configId", "annotationIds", "label", "ambiguity", "rationaleCode",
  ]);
  const annotationIds = snapshotArray(required(record, "annotationIds", `${pathValue}.annotationIds`), `${pathValue}.annotationIds`, 2, 2)
    .map((value, index) => {
      if (typeof value !== "string") fail(`${pathValue}.annotationIds[${index}]`, "string_required");
      return validateEvalId(value, `${pathValue}.annotationIds[${index}]`);
    })
    .sort(rawStringCompare) as [string, string];
  assertUnique(annotationIds, `${pathValue}.annotationIds`, "unique_annotation_ids_required");
  return Object.freeze({
    adjudicationId: evalIdField(record, "adjudicationId", `${pathValue}.adjudicationId`),
    actorId: evalIdField(record, "actorId", `${pathValue}.actorId`),
    configId: evalIdField(record, "configId", `${pathValue}.configId`),
    annotationIds: Object.freeze(annotationIds),
    label: validateLabel(required(record, "label", `${pathValue}.label`), `${pathValue}.label`),
    ambiguity: requiredBoolean(record, "ambiguity", `${pathValue}.ambiguity`),
    rationaleCode: requiredEnum(record, "rationaleCode", `${pathValue}.rationaleCode`, RATIONALE_CODES),
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
    "generalizedContext",
  ]);
  requiredLiteral(record, "schemaVersion", `${pathValue}.schemaVersion`, STAGE_A_FILTER_SCHEMA_VERSION);
  return Object.freeze({
    schemaVersion: STAGE_A_FILTER_SCHEMA_VERSION,
    filterId: evalIdField(record, "filterId", `${pathValue}.filterId`),
    source: requiredLiteral(record, "source", `${pathValue}.source`, "production_deidentified"),
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
    "configId",
  ]);
  return Object.freeze({
    origin: requiredLiteral(record, "origin", `${pathValue}.origin`, "agent_synthetic"),
    authorId: evalIdField(record, "authorId", `${pathValue}.authorId`),
    configId: evalIdField(record, "configId", `${pathValue}.configId`),
  });
}

function validateBundle(input: unknown, pathValue: string): StageABundleV2 {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion",
    "bundleId",
    "filterId",
    "cohort",
    "persona",
    "promptLocale",
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
    promptLocale: requiredEnum(record, "promptLocale", `${pathValue}.promptLocale`, LOCALES),
    softQuery: validateSoftQuery(required(record, "softQuery", `${pathValue}.softQuery`), `${pathValue}.softQuery`),
    promptProvenance: validatePromptProvenance(required(record, "promptProvenance", `${pathValue}.promptProvenance`), `${pathValue}.promptProvenance`),
  });
}

function requiredInteger(record: Record<string, unknown>, field: string, pathValue: string, minimum: number, maximum: number): number {
  const value = required(record, field, pathValue);
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) fail(pathValue, "bounded_integer_required");
  return value as number;
}

function validateWholeSecondInstant(value: unknown, pathValue: string): string {
  if (typeof value !== "string") fail(pathValue, "canonical_instant_required");
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString() !== value || parsed.getUTCMilliseconds() !== 0) fail(pathValue, "canonical_instant_required");
  return value;
}

const PAIR_BASE_FIELDS = [
    "schemaVersion",
    "pairId",
    "bundleId",
    "position",
    "sourceRank",
    "postingFirstSeenAt",
    "sourceSnapshotIdentity",
    "locale",
    "evidenceCondition",
    "classifierSource",
    "contentIdentity",
] as const;

function validatePairBase(record: Record<string, unknown>, pathValue: string): StageAPreAnnotationPairV2 {
  requiredLiteral(record, "schemaVersion", `${pathValue}.schemaVersion`, STAGE_A_PAIR_SCHEMA_VERSION);
  const classifierSource = validateClassifierSource(required(record, "classifierSource", `${pathValue}.classifierSource`), `${pathValue}.classifierSource`);
  const normalized = normalizeClassifierSource(classifierSource, `${pathValue}.classifierSource`);
  const contentIdentity = validateDigest(required(record, "contentIdentity", `${pathValue}.contentIdentity`), `${pathValue}.contentIdentity`);
  if (normalized.contentIdentity !== contentIdentity) fail(`${pathValue}.contentIdentity`, "content_identity_mismatch");
  return Object.freeze({
    schemaVersion: STAGE_A_PAIR_SCHEMA_VERSION,
    pairId: evalIdField(record, "pairId", `${pathValue}.pairId`),
    bundleId: evalIdField(record, "bundleId", `${pathValue}.bundleId`),
    position: requiredInteger(record, "position", `${pathValue}.position`, 0, 7),
    sourceRank: requiredInteger(record, "sourceRank", `${pathValue}.sourceRank`, 0, 1_000_000),
    postingFirstSeenAt: validateWholeSecondInstant(required(record, "postingFirstSeenAt", `${pathValue}.postingFirstSeenAt`), `${pathValue}.postingFirstSeenAt`),
    sourceSnapshotIdentity: validateDigest(required(record, "sourceSnapshotIdentity", `${pathValue}.sourceSnapshotIdentity`), `${pathValue}.sourceSnapshotIdentity`),
    locale: requiredEnum(record, "locale", `${pathValue}.locale`, LOCALES),
    evidenceCondition: requiredEnum(record, "evidenceCondition", `${pathValue}.evidenceCondition`, EVIDENCE_CONDITIONS),
    classifierSource,
    contentIdentity,
  });
}

function validatePreAnnotationPair(input: unknown, pathValue: string): StageAPreAnnotationPairV2 {
  return validatePairBase(snapshotRecord(input, pathValue, PAIR_BASE_FIELDS), pathValue);
}

function validatePair(input: unknown, pathValue: string): StageAPairV2 {
  const record = snapshotRecord(input, pathValue, [
    ...PAIR_BASE_FIELDS,
    "annotations",
    "adjudication",
  ]);
  const base = validatePairBase(record, pathValue);
  const annotations = snapshotArray(required(record, "annotations", `${pathValue}.annotations`), `${pathValue}.annotations`, 2, 2)
    .map((annotation, index) => validateAnnotation(annotation, `${pathValue}.annotations[${index}]`))
    .sort((left, right) => rawStringCompare(left.annotationId, right.annotationId)) as [StageAAnnotationV2, StageAAnnotationV2];
  if (annotations[0].annotationId === annotations[1].annotationId) {
    fail(`${pathValue}.annotations`, "unique_annotation_ids_required");
  }
  if (annotations[0].actorId === annotations[1].actorId) {
    fail(`${pathValue}.annotations`, "blind_distinct_annotators_required");
  }
  const adjudication = validateAdjudication(required(record, "adjudication", `${pathValue}.adjudication`), `${pathValue}.adjudication`);
  const annotationDisagreement = annotations[0].label !== annotations[1].label || annotations[0].ambiguity !== annotations[1].ambiguity;
  const needsAdjudication = annotationDisagreement || annotations.some(({ ambiguity }) => ambiguity) || base.evidenceCondition === "policy_boundary";
  if (needsAdjudication !== Boolean(adjudication)) {
    fail(`${pathValue}.adjudication`, needsAdjudication ? "adjudication_required" : "unnecessary_adjudication_forbidden");
  }
  if (adjudication && annotations.some(({ actorId }) => actorId === adjudication.actorId)) {
    fail(`${pathValue}.adjudication`, "independent_adjudicator_required");
  }
  if (adjudication && canonicalStageAJson(adjudication.annotationIds) !== canonicalStageAJson(annotations.map(({ annotationId }) => annotationId))) {
    fail(`${pathValue}.adjudication.annotationIds`, "annotation_linkage_required");
  }
  return Object.freeze({
    ...base,
    annotations: Object.freeze(annotations),
    adjudication,
  });
}

function validateFinalCritic(input: unknown, pathValue: string): StageAFinalCriticV2 {
  const record = snapshotRecord(input, pathValue, ["reviewId", "actorId", "configId", "reviewedWipDigest", "approved"]);
  return Object.freeze({
    reviewId: evalIdField(record, "reviewId", `${pathValue}.reviewId`),
    actorId: evalIdField(record, "actorId", `${pathValue}.actorId`),
    configId: evalIdField(record, "configId", `${pathValue}.configId`),
    reviewedWipDigest: validateDigest(required(record, "reviewedWipDigest", `${pathValue}.reviewedWipDigest`), `${pathValue}.reviewedWipDigest`),
    approved: requiredLiteral(record, "approved", `${pathValue}.approved`, true),
  });
}

function assertUnique(values: readonly string[], pathValue: string, rule: string): void {
  if (new Set(values).size !== values.length) fail(pathValue, rule);
}

export function validateStageACalibrationArtifact(
  input: unknown,
): StageAValidatedCalibrationArtifactV2 {
  const record = snapshotRecord(input, "$calibration", [
    "schemaVersion",
    "calibrationId",
    "examples",
  ]);
  requiredLiteral(
    record,
    "schemaVersion",
    "$calibration.schemaVersion",
    "ai-filter-stage-a-calibration-v2",
  );
  const examples = snapshotArray(
    required(record, "examples", "$calibration.examples"),
    "$calibration.examples",
    STAGE_A_CALIBRATION_MIN_EXAMPLES,
    STAGE_A_CALIBRATION_MAX_EXAMPLES,
  ).map((inputExample, index) => {
    const examplePath = `$calibration.examples[${index}]`;
    const example = snapshotRecord(inputExample, examplePath, [
      "calibrationExampleId",
      "softQuery",
      "classifierSource",
      "contentIdentity",
    ]);
    const classifierSource = validateClassifierSource(
      required(example, "classifierSource", `${examplePath}.classifierSource`),
      `${examplePath}.classifierSource`,
    );
    const normalized = normalizeClassifierSource(
      classifierSource,
      `${examplePath}.classifierSource`,
    );
    const contentIdentity = validateDigest(
      required(example, "contentIdentity", `${examplePath}.contentIdentity`),
      `${examplePath}.contentIdentity`,
    );
    if (contentIdentity !== normalized.contentIdentity) {
      fail(`${examplePath}.contentIdentity`, "content_identity_mismatch");
    }
    return Object.freeze({
      calibrationExampleId: evalIdField(
        example,
        "calibrationExampleId",
        `${examplePath}.calibrationExampleId`,
      ),
      softQuery: validateSoftQuery(
        required(example, "softQuery", `${examplePath}.softQuery`),
        `${examplePath}.softQuery`,
      ),
      classifierInput: normalized.payload,
    });
  }).sort((left, right) => rawStringCompare(left.calibrationExampleId, right.calibrationExampleId));
  assertUnique(
    examples.map(({ calibrationExampleId }) => calibrationExampleId),
    "$calibration.examples",
    "unique_calibration_example_ids_required",
  );
  return Object.freeze({
    calibrationId: evalIdField(record, "calibrationId", "$calibration.calibrationId"),
    examples: Object.freeze(examples),
  });
}

export function digestStageACalibrationArtifact(input: unknown): string {
  return domainDigest(
    "ai-filter-stage-a-calibration-v2",
    validateStageACalibrationArtifact(input),
  );
}

export function digestStageAResolvedCalibrationGroundTruth(
  decisions: StageACalibrationResultV2["humanReview"]["decisions"],
): string {
  const resolved = decisions
    .filter(({ judgment }) => judgment !== "unclear")
    .map(({ calibrationExampleId, judgment }) => ({ calibrationExampleId, judgment }))
    .sort((left, right) => rawStringCompare(left.calibrationExampleId, right.calibrationExampleId));
  return domainDigest("ai-filter-stage-a-resolved-ground-truth-v2", resolved);
}

function validateAgentConfig(input: unknown, pathValue: string): StageAAgentConfigV2 {
  const record = snapshotRecord(input, pathValue, [
    "configId", "role", "model", "modelVersion", "reasoningEffort", "taskPromptDigest",
  ]);
  return Object.freeze({
    configId: evalIdField(record, "configId", `${pathValue}.configId`),
    role: requiredEnum(record, "role", `${pathValue}.role`, AGENT_ROLES),
    model: provenanceTokenField(record, "model", `${pathValue}.model`),
    modelVersion: provenanceTokenField(record, "modelVersion", `${pathValue}.modelVersion`),
    reasoningEffort: requiredEnum(record, "reasoningEffort", `${pathValue}.reasoningEffort`, REASONING_EFFORTS),
    taskPromptDigest: validateDigest(required(record, "taskPromptDigest", `${pathValue}.taskPromptDigest`), `${pathValue}.taskPromptDigest`),
  });
}

function normalizeStageACalibrationResult(input: unknown): StageACalibrationResultV2 {
  const record = snapshotRecord(input, "$calibrationResult", [
    "schemaVersion", "calibrationId", "calibrationInputDigest", "humanReview",
    "candidateConfigs", "trials", "selectedConfigs",
  ]);
  requiredLiteral(record, "schemaVersion", "$calibrationResult.schemaVersion", STAGE_A_CALIBRATION_RESULT_SCHEMA_VERSION);
  const reviewRecord = snapshotRecord(required(record, "humanReview", "$calibrationResult.humanReview"), "$calibrationResult.humanReview", ["reviewId", "reviewerId", "approved", "decisions"]);
  requiredLiteral(reviewRecord, "approved", "$calibrationResult.humanReview.approved", true);
  const decisions = snapshotArray(required(reviewRecord, "decisions", "$calibrationResult.humanReview.decisions"), "$calibrationResult.humanReview.decisions", 24, 32)
    .map((inputDecision, index) => {
      const itemPath = `$calibrationResult.humanReview.decisions[${index}]`;
      const decision = snapshotRecord(inputDecision, itemPath, ["calibrationExampleId", "judgment"]);
      return Object.freeze({
        calibrationExampleId: evalIdField(decision, "calibrationExampleId", `${itemPath}.calibrationExampleId`),
        judgment: requiredEnum(decision, "judgment", `${itemPath}.judgment`, ["accept", "reject", "unclear"]),
      });
    })
    .sort((left, right) => rawStringCompare(left.calibrationExampleId, right.calibrationExampleId));
  assertUnique(decisions.map(({ calibrationExampleId }) => calibrationExampleId), "$calibrationResult.humanReview.decisions", "unique_calibration_decisions_required");
  const resolvedDecisions = decisions.filter(({ judgment }) => judgment !== "unclear");
  if (resolvedDecisions.length < STAGE_A_CALIBRATION_MIN_EXAMPLES) {
    fail("$calibrationResult.humanReview.decisions", "minimum_resolved_calibration_decisions_required");
  }
  const resolvedGroundTruthDigest = digestStageAResolvedCalibrationGroundTruth(decisions);
  const configs = snapshotArray(required(record, "candidateConfigs", "$calibrationResult.candidateConfigs"), "$calibrationResult.candidateConfigs", AGENT_ROLES.length * 2, AGENT_ROLES.length * 4)
    .map((config, index) => validateAgentConfig(config, `$calibrationResult.candidateConfigs[${index}]`))
    .sort((left, right) => rawStringCompare(left.configId, right.configId));
  assertUnique(configs.map(({ configId }) => configId), "$calibrationResult.candidateConfigs", "unique_config_ids_required");
  for (const role of AGENT_ROLES) {
    if (configs.filter((config) => config.role === role).length < 2) {
      fail("$calibrationResult.candidateConfigs", "two_candidate_configs_per_role_required");
    }
  }
  const trials = snapshotArray(required(record, "trials", "$calibrationResult.trials"), "$calibrationResult.trials", configs.length, configs.length)
    .map((trialInput, index): StageACalibrationTrialV2 => {
      const pathValue = `$calibrationResult.trials[${index}]`;
      const trial = snapshotRecord(trialInput, pathValue, [
        "trialId", "configId", "role", "suiteKind", "suiteInputDigest", "outputDigest",
        "sampleCount", "blindedScore", "spotCheck",
      ]);
      const role = requiredEnum(trial, "role", `${pathValue}.role`, AGENT_ROLES);
      const suiteKind = requiredEnum(trial, "suiteKind", `${pathValue}.suiteKind`, CALIBRATION_SUITE_KINDS);
      if (suiteKind !== CALIBRATION_SUITE_BY_ROLE[role]) fail(`${pathValue}.suiteKind`, "role_specific_calibration_suite_required");
      const sampleCount = requiredInteger(trial, "sampleCount", `${pathValue}.sampleCount`, 1, 10_000);
      if (role === "prompt_author" && sampleCount !== 8) fail(`${pathValue}.sampleCount`, "same_eight_disposable_feeds_required");
      if (role === "annotator" && sampleCount !== resolvedDecisions.length) fail(`${pathValue}.sampleCount`, "resolved_ground_truth_sample_count_required");
      const suiteInputDigest = validateDigest(required(trial, "suiteInputDigest", `${pathValue}.suiteInputDigest`), `${pathValue}.suiteInputDigest`);
      if (role === "annotator" && suiteInputDigest !== resolvedGroundTruthDigest) fail(`${pathValue}.suiteInputDigest`, "resolved_ground_truth_digest_required");
      const spotCheckRecord = snapshotRecord(required(trial, "spotCheck", `${pathValue}.spotCheck`), `${pathValue}.spotCheck`, ["disposition", "failureCodes"]);
      const disposition = requiredEnum(spotCheckRecord, "disposition", `${pathValue}.spotCheck.disposition`, ["pass", "fail"]);
      const failureCodes = snapshotArray(required(spotCheckRecord, "failureCodes", `${pathValue}.spotCheck.failureCodes`), `${pathValue}.spotCheck.failureCodes`, disposition === "pass" ? 0 : 1, 16)
        .map((failureCode, failureIndex) => {
          const failurePath = `${pathValue}.spotCheck.failureCodes[${failureIndex}]`;
          if (typeof failureCode !== "string" || !PROVENANCE_TOKEN_PATTERN.test(failureCode)) fail(failurePath, "provenance_token_required");
          return failureCode;
        })
        .sort(rawStringCompare);
      if (disposition === "pass" && failureCodes.length !== 0) fail(`${pathValue}.spotCheck.failureCodes`, "passing_spot_check_has_no_failures_required");
      assertUnique(failureCodes, `${pathValue}.spotCheck.failureCodes`, "unique_failure_codes_required");
      return Object.freeze({
        trialId: evalIdField(trial, "trialId", `${pathValue}.trialId`),
        configId: evalIdField(trial, "configId", `${pathValue}.configId`),
        role,
        suiteKind,
        suiteInputDigest,
        outputDigest: validateDigest(required(trial, "outputDigest", `${pathValue}.outputDigest`), `${pathValue}.outputDigest`),
        sampleCount,
        blindedScore: requiredInteger(trial, "blindedScore", `${pathValue}.blindedScore`, 0, 10_000),
        spotCheck: Object.freeze({ disposition, failureCodes: Object.freeze(failureCodes) }),
      });
    })
    .sort((left, right) => rawStringCompare(left.configId, right.configId));
  assertUnique(trials.map(({ trialId }) => trialId), "$calibrationResult.trials", "unique_trial_ids_required");
  assertUnique(trials.map(({ configId }) => configId), "$calibrationResult.trials", "one_trial_per_candidate_config_required");
  assertUnique(trials.map(({ outputDigest }) => outputDigest), "$calibrationResult.trials", "unique_trial_output_digests_required");
  if (canonicalStageAJson(trials.map(({ configId }) => configId)) !== canonicalStageAJson(configs.map(({ configId }) => configId))) {
    fail("$calibrationResult.trials", "every_candidate_config_must_be_exercised");
  }
  const configById = new Map(configs.map((config) => [config.configId, config]));
  for (const trial of trials) {
    if (configById.get(trial.configId)?.role !== trial.role) fail("$calibrationResult.trials", "trial_config_role_mismatch");
  }
  for (const role of AGENT_ROLES) {
    const suiteDigests = new Set(trials.filter((trial) => trial.role === role).map(({ suiteInputDigest }) => suiteInputDigest));
    if (suiteDigests.size !== 1) fail("$calibrationResult.trials", "same_role_suite_input_required");
  }
  const selectedConfigs = snapshotArray(required(record, "selectedConfigs", "$calibrationResult.selectedConfigs"), "$calibrationResult.selectedConfigs", AGENT_ROLES.length, AGENT_ROLES.length)
    .map((selectionInput, index): StageASelectedConfigV2 => {
      const pathValue = `$calibrationResult.selectedConfigs[${index}]`;
      const selection = snapshotRecord(selectionInput, pathValue, ["role", "configId", "selectionDisposition"]);
      return Object.freeze({
        role: requiredEnum(selection, "role", `${pathValue}.role`, AGENT_ROLES),
        configId: evalIdField(selection, "configId", `${pathValue}.configId`),
        selectionDisposition: requiredLiteral(selection, "selectionDisposition", `${pathValue}.selectionDisposition`, "approved"),
      });
    })
    .sort((left, right) => rawStringCompare(left.role, right.role));
  assertUnique(selectedConfigs.map(({ role }) => role), "$calibrationResult.selectedConfigs", "one_selected_config_per_role_required");
  assertUnique(selectedConfigs.map(({ configId }) => configId), "$calibrationResult.selectedConfigs", "unique_selected_config_ids_required");
  if (selectedConfigs.some(({ role }, index) => role !== [...AGENT_ROLES].sort(rawStringCompare)[index])) fail("$calibrationResult.selectedConfigs", "complete_role_configs_required");
  const trialByConfigId = new Map(trials.map((trial) => [trial.configId, trial]));
  for (const selection of selectedConfigs) {
    const config = configById.get(selection.configId);
    const trial = trialByConfigId.get(selection.configId);
    if (config?.role !== selection.role) fail("$calibrationResult.selectedConfigs", "selected_candidate_role_mismatch");
    if (!trial || trial.spotCheck.disposition !== "pass") fail("$calibrationResult.selectedConfigs", "selected_candidate_passing_trial_required");
  }
  return Object.freeze({
    schemaVersion: STAGE_A_CALIBRATION_RESULT_SCHEMA_VERSION,
    calibrationId: evalIdField(record, "calibrationId", "$calibrationResult.calibrationId"),
    calibrationInputDigest: validateDigest(required(record, "calibrationInputDigest", "$calibrationResult.calibrationInputDigest"), "$calibrationResult.calibrationInputDigest"),
    humanReview: Object.freeze({
      reviewId: evalIdField(reviewRecord, "reviewId", "$calibrationResult.humanReview.reviewId"),
      reviewerId: evalIdField(reviewRecord, "reviewerId", "$calibrationResult.humanReview.reviewerId"),
      approved: true,
      decisions: Object.freeze(decisions),
    }),
    candidateConfigs: Object.freeze(configs),
    trials: Object.freeze(trials),
    selectedConfigs: Object.freeze(selectedConfigs),
  });
}

export function validateStageACalibrationResult(
  input: unknown,
  calibrationArtifactInput: unknown,
  expectedCalibrationInputDigest: string,
): StageACalibrationResultV2 {
  validateDigest(expectedCalibrationInputDigest, "$expectedCalibrationInputDigest");
  const calibration = validateStageACalibrationArtifact(calibrationArtifactInput);
  if (digestStageACalibrationArtifact(calibrationArtifactInput) !== expectedCalibrationInputDigest) {
    fail("$calibration", "calibration_input_pin_mismatch");
  }
  const result = normalizeStageACalibrationResult(input);
  if (result.calibrationInputDigest !== expectedCalibrationInputDigest) {
    fail("$calibrationResult.calibrationInputDigest", "calibration_input_pin_mismatch");
  }
  if (result.calibrationId !== calibration.calibrationId) {
    fail("$calibrationResult.calibrationId", "calibration_id_mismatch");
  }
  const exampleIds = calibration.examples.map(({ calibrationExampleId }) => calibrationExampleId);
  const decisionIds = result.humanReview.decisions.map(({ calibrationExampleId }) => calibrationExampleId);
  if (canonicalStageAJson(exampleIds) !== canonicalStageAJson(decisionIds)) {
    fail("$calibrationResult.humanReview.decisions", "complete_calibration_decisions_required");
  }
  return result;
}

export function digestStageACalibrationResult(
  input: unknown,
  calibrationArtifactInput: unknown,
  expectedCalibrationInputDigest: string,
): string {
  return domainDigest(
    STAGE_A_CALIBRATION_RESULT_SCHEMA_VERSION,
    validateStageACalibrationResult(
      input,
      calibrationArtifactInput,
      expectedCalibrationInputDigest,
    ),
  );
}

function validateSourcePin(
  input: unknown,
  pathValue: string,
  expectedSourcePath: string,
  expectedExportName: string,
): StageASourcePinV2 {
  const record = snapshotRecord(input, pathValue, ["sourcePath", "exportName", "sourceDigest"]);
  return Object.freeze({
    sourcePath: requiredLiteral(record, "sourcePath", `${pathValue}.sourcePath`, expectedSourcePath),
    exportName: requiredLiteral(record, "exportName", `${pathValue}.exportName`, expectedExportName),
    sourceDigest: validateDigest(required(record, "sourceDigest", `${pathValue}.sourceDigest`), `${pathValue}.sourceDigest`),
  });
}

function validateNormalizerPin(
  input: unknown,
  pathValue: string,
  expectedSourcePath: string,
  expectedExportName: string,
  expectedVersion: string | number,
): StageANormalizerPinV2 {
  const record = snapshotRecord(input, pathValue, [
    "sourcePath", "exportName", "sourceDigest", "version", "fallbackPolicyDigest",
    "truncationPolicyDigest",
  ]);
  return Object.freeze({
    sourcePath: requiredLiteral(record, "sourcePath", `${pathValue}.sourcePath`, expectedSourcePath),
    exportName: requiredLiteral(record, "exportName", `${pathValue}.exportName`, expectedExportName),
    sourceDigest: validateDigest(required(record, "sourceDigest", `${pathValue}.sourceDigest`), `${pathValue}.sourceDigest`),
    version: requiredLiteral(record, "version", `${pathValue}.version`, expectedVersion),
    fallbackPolicyDigest: validateDigest(required(record, "fallbackPolicyDigest", `${pathValue}.fallbackPolicyDigest`), `${pathValue}.fallbackPolicyDigest`),
    truncationPolicyDigest: validateDigest(required(record, "truncationPolicyDigest", `${pathValue}.truncationPolicyDigest`), `${pathValue}.truncationPolicyDigest`),
  });
}

export function validateStageAExtractionManifest(input: unknown, pathValue = "$extractionManifest"): StageAExtractionManifestV2 {
  const record = snapshotRecord(input, pathValue, [
    "schemaVersion", "repositoryCommit", "af1ContractVersion", "typesense", "compiler",
    "reader", "dependencyLockDigest", "compiledQueries", "query", "classifierNormalizer",
    "softQueryNormalizer",
  ]);
  const repositoryCommit = requiredString(record, "repositoryCommit", `${pathValue}.repositoryCommit`, 40);
  if (!/^[a-f0-9]{40}$/u.test(repositoryCommit)) fail(`${pathValue}.repositoryCommit`, "repository_oid_required");
  const typesenseRecord = snapshotRecord(required(record, "typesense", `${pathValue}.typesense`), `${pathValue}.typesense`, ["collectionAlias", "resolvedCollection", "snapshotDigest"]);
  const resolvedCollection = requiredString(typesenseRecord, "resolvedCollection", `${pathValue}.typesense.resolvedCollection`, 128);
  if (!/^job_posting_v[1-9]\d*$/u.test(resolvedCollection)) fail(`${pathValue}.typesense.resolvedCollection`, "versioned_collection_required");
  const queryRecord = snapshotRecord(required(record, "query", `${pathValue}.query`), `${pathValue}.query`, [
    "templateDigest", "order", "pageSize", "requestedStrictLowerBound", "effectiveWindowStart",
    "cutoff", "requestedBoundary", "effectiveBoundary", "productionSelection", "challengeSelection",
  ]);
  const requestedStrictLowerBound = validateWholeSecondInstant(required(queryRecord, "requestedStrictLowerBound", `${pathValue}.query.requestedStrictLowerBound`), `${pathValue}.query.requestedStrictLowerBound`);
  const effectiveWindowStart = validateWholeSecondInstant(required(queryRecord, "effectiveWindowStart", `${pathValue}.query.effectiveWindowStart`), `${pathValue}.query.effectiveWindowStart`);
  const cutoff = validateWholeSecondInstant(required(queryRecord, "cutoff", `${pathValue}.query.cutoff`), `${pathValue}.query.cutoff`);
  const requestedTime = new Date(requestedStrictLowerBound).getTime();
  if (new Date(cutoff).getTime() - requestedTime !== 30 * 24 * 60 * 60 * 1_000) fail(`${pathValue}.query`, "exact_30_day_requested_window_required");
  if (new Date(effectiveWindowStart).getTime() !== requestedTime + 1_000) fail(`${pathValue}.query`, "strict_lower_bound_translation_required");
  const compiledQueries = snapshotArray(required(record, "compiledQueries", `${pathValue}.compiledQueries`), `${pathValue}.compiledQueries`, STAGE_A_REQUIRED_FILTERS, STAGE_A_REQUIRED_FILTERS)
    .map((compiledInput, index) => {
      const compiledPath = `${pathValue}.compiledQueries[${index}]`;
      const compiled = snapshotRecord(compiledInput, compiledPath, ["filterId", "fingerprint"]);
      const fingerprint = snapshotRecord(required(compiled, "fingerprint", `${compiledPath}.fingerprint`), `${compiledPath}.fingerprint`, ["scheme", "value"]);
      return Object.freeze({
        filterId: evalIdField(compiled, "filterId", `${compiledPath}.filterId`),
        fingerprint: Object.freeze({
          scheme: requiredLiteral(fingerprint, "scheme", `${compiledPath}.fingerprint.scheme`, "hmac-sha256-v1"),
          value: validateDigest(required(fingerprint, "value", `${compiledPath}.fingerprint.value`), `${compiledPath}.fingerprint.value`),
        }),
      });
    })
    .sort((left, right) => rawStringCompare(left.filterId, right.filterId));
  assertUnique(compiledQueries.map(({ filterId }) => filterId), `${pathValue}.compiledQueries`, "unique_filter_ids_required");
  assertUnique(compiledQueries.map(({ fingerprint }) => fingerprint.value), `${pathValue}.compiledQueries`, "unique_compiled_query_fingerprints_required");
  return Object.freeze({
    schemaVersion: requiredLiteral(record, "schemaVersion", `${pathValue}.schemaVersion`, STAGE_A_EXTRACTION_MANIFEST_SCHEMA_VERSION),
    repositoryCommit,
    af1ContractVersion: requiredLiteral(record, "af1ContractVersion", `${pathValue}.af1ContractVersion`, 1),
    typesense: Object.freeze({
      collectionAlias: requiredLiteral(typesenseRecord, "collectionAlias", `${pathValue}.typesense.collectionAlias`, "job_posting"),
      resolvedCollection,
      snapshotDigest: validateDigest(required(typesenseRecord, "snapshotDigest", `${pathValue}.typesense.snapshotDigest`), `${pathValue}.typesense.snapshotDigest`),
    }),
    compiler: validateSourcePin(required(record, "compiler", `${pathValue}.compiler`), `${pathValue}.compiler`, STAGE_A_COMPILER_SOURCE_PATH, STAGE_A_COMPILER_EXPORT),
    reader: validateSourcePin(required(record, "reader", `${pathValue}.reader`), `${pathValue}.reader`, STAGE_A_READER_SOURCE_PATH, STAGE_A_READER_EXPORT),
    dependencyLockDigest: validateDigest(required(record, "dependencyLockDigest", `${pathValue}.dependencyLockDigest`), `${pathValue}.dependencyLockDigest`),
    compiledQueries: Object.freeze(compiledQueries),
    query: Object.freeze({
      templateDigest: validateDigest(required(queryRecord, "templateDigest", `${pathValue}.query.templateDigest`), `${pathValue}.query.templateDigest`),
      order: requiredLiteral(queryRecord, "order", `${pathValue}.query.order`, "first_seen_at_desc_candidate_id_asc"),
      pageSize: requiredLiteral(queryRecord, "pageSize", `${pathValue}.query.pageSize`, 8),
      requestedStrictLowerBound,
      effectiveWindowStart,
      cutoff,
      requestedBoundary: requiredLiteral(queryRecord, "requestedBoundary", `${pathValue}.query.requestedBoundary`, "(requestedStrictLowerBound,cutoff)"),
      effectiveBoundary: requiredLiteral(queryRecord, "effectiveBoundary", `${pathValue}.query.effectiveBoundary`, "[effectiveWindowStart,cutoff)"),
      productionSelection: requiredLiteral(queryRecord, "productionSelection", `${pathValue}.query.productionSelection`, "first_eight"),
      challengeSelection: requiredLiteral(queryRecord, "challengeSelection", `${pathValue}.query.challengeSelection`, "frozen_source_rank"),
    }),
    classifierNormalizer: validateNormalizerPin(required(record, "classifierNormalizer", `${pathValue}.classifierNormalizer`), `${pathValue}.classifierNormalizer`, STAGE_A_CLASSIFIER_NORMALIZER_SOURCE_PATH, STAGE_A_CLASSIFIER_NORMALIZER_EXPORT, CLASSIFIER_INPUT_NORMALIZER_VERSION),
    softQueryNormalizer: validateNormalizerPin(required(record, "softQueryNormalizer", `${pathValue}.softQueryNormalizer`), `${pathValue}.softQueryNormalizer`, STAGE_A_SOFT_QUERY_NORMALIZER_SOURCE_PATH, STAGE_A_SOFT_QUERY_NORMALIZER_EXPORT, AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION),
  });
}

export function digestStageAExtractionManifest(input: unknown): string {
  return domainDigest(STAGE_A_EXTRACTION_MANIFEST_SCHEMA_VERSION, validateStageAExtractionManifest(input));
}

function validateReviewPlan(input: unknown, pathValue: string): StageAReviewPlanV2 {
  const record = snapshotRecord(input, pathValue, ["promptReviewSeed", "auditSeed", "auditRule", "auditSize"]);
  return Object.freeze({
    promptReviewSeed: validateDigest(required(record, "promptReviewSeed", `${pathValue}.promptReviewSeed`), `${pathValue}.promptReviewSeed`),
    auditSeed: validateDigest(required(record, "auditSeed", `${pathValue}.auditSeed`), `${pathValue}.auditSeed`),
    auditRule: requiredLiteral(record, "auditRule", `${pathValue}.auditRule`, "bounded-16-8-8-v2"),
    auditSize: requiredLiteral(record, "auditSize", `${pathValue}.auditSize`, STAGE_A_HUMAN_AUDIT_PAIRS),
  });
}

function assertCorpusShape(
  filters: readonly StageAFilterV2[],
  bundles: readonly StageABundleV2[],
  pairs: readonly StageAPreAnnotationPairV2[],
  pathPrefix: string,
): void {
  assertUnique(filters.map(({ filterId }) => filterId), `${pathPrefix}.filters`, "unique_filter_ids_required");
  assertUnique(bundles.map(({ bundleId }) => bundleId), `${pathPrefix}.bundles`, "unique_bundle_ids_required");
  assertUnique(bundles.map(({ softQuery }) => softQuery), `${pathPrefix}.bundles`, "unique_queries_required");
  assertUnique(pairs.map(({ pairId }) => pairId), `${pathPrefix}.pairs`, "unique_pair_ids_required");
  const filterIds = new Set(filters.map(({ filterId }) => filterId));
  if (bundles.some(({ filterId }) => !filterIds.has(filterId))) fail(`${pathPrefix}.bundles`, "known_filter_reference_required");
  const productionBundles = bundles.filter(({ cohort }) => cohort === "production_shaped");
  const challengeBundles = bundles.filter(({ cohort }) => cohort === "challenge");
  if (productionBundles.length !== 15 || challengeBundles.length !== 10) fail(`${pathPrefix}.bundles`, "exact_cohort_split_required");
  for (const filter of filters) {
    const uses = bundles.filter(({ filterId }) => filterId === filter.filterId);
    if (uses.length === 1 && uses[0].cohort === "production_shaped") continue;
    if (uses.length === 2 && uses.every(({ cohort }) => cohort === "challenge")) continue;
    fail(`${pathPrefix}.bundles`, "disjoint_15_plus_5_filter_mapping_required");
  }
  const bundleIds = new Set(bundles.map(({ bundleId }) => bundleId));
  if (pairs.some(({ bundleId }) => !bundleIds.has(bundleId))) fail(`${pathPrefix}.pairs`, "known_bundle_reference_required");
  for (const bundle of bundles) {
    const feed = pairs.filter(({ bundleId }) => bundleId === bundle.bundleId).sort((left, right) => left.position - right.position);
    if (feed.length !== 8 || feed.some(({ position }, index) => position !== index)) fail(`${pathPrefix}.pairs`, "exact_feed_positions_required");
    assertUnique(feed.map(({ contentIdentity }) => contentIdentity), `${pathPrefix}.pairs`, "unique_candidates_per_bundle_required");
    assertUnique(feed.map(({ sourceSnapshotIdentity }) => sourceSnapshotIdentity), `${pathPrefix}.pairs`, "unique_source_snapshots_per_bundle_required");
  }
  for (const cohort of COHORTS) {
    const cohortBundles = bundles.filter((bundle) => bundle.cohort === cohort);
    if (new Set(cohortBundles.map(({ promptLocale }) => promptLocale)).size !== LOCALES.length) fail(`${pathPrefix}.bundles`, "cohort_locale_diversity_required");
    if (new Set(cohortBundles.map(({ persona }) => persona)).size < 5) fail(`${pathPrefix}.bundles`, "cohort_persona_diversity_required");
    const cohortBundleIds = new Set(cohortBundles.map(({ bundleId }) => bundleId));
    const cohortPairs = pairs.filter(({ bundleId }) => cohortBundleIds.has(bundleId));
    for (const evidence of EVIDENCE_CONDITIONS) {
      if (cohortPairs.filter(({ evidenceCondition }) => evidenceCondition === evidence).length < 4) fail(`${pathPrefix}.pairs`, "cohort_evidence_diversity_required");
    }
  }
  for (const persona of PERSONAS) {
    if (bundles.filter((bundle) => bundle.persona === persona).length < 2) fail(`${pathPrefix}.bundles`, "persona_minimum_required");
  }
}

export function validateStageAPreAnnotation(input: unknown): StageAPreAnnotationV2 {
  const record = snapshotRecord(input, "$pre", [
    "schemaVersion", "datasetId", "classifierInputSchemaVersion", "classifierInputNormalizerVersion",
    "softQueryNormalizerVersion", "calibrationResultDigest", "extractionManifest",
    "extractionManifestDigest", "reviewPlan",
    "filters", "bundles", "pairs",
  ]);
  requiredLiteral(record, "schemaVersion", "$pre.schemaVersion", STAGE_A_PRE_ANNOTATION_SCHEMA_VERSION);
  requiredLiteral(record, "classifierInputSchemaVersion", "$pre.classifierInputSchemaVersion", CLASSIFIER_INPUT_SCHEMA_VERSION);
  requiredLiteral(record, "classifierInputNormalizerVersion", "$pre.classifierInputNormalizerVersion", CLASSIFIER_INPUT_NORMALIZER_VERSION);
  requiredLiteral(record, "softQueryNormalizerVersion", "$pre.softQueryNormalizerVersion", AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION);
  const extractionManifest = validateStageAExtractionManifest(required(record, "extractionManifest", "$pre.extractionManifest"), "$pre.extractionManifest");
  const extractionManifestDigest = validateDigest(required(record, "extractionManifestDigest", "$pre.extractionManifestDigest"), "$pre.extractionManifestDigest");
  if (extractionManifestDigest !== digestStageAExtractionManifest(extractionManifest)) fail("$pre.extractionManifestDigest", "extraction_manifest_digest_mismatch");
  const reviewPlan = validateReviewPlan(required(record, "reviewPlan", "$pre.reviewPlan"), "$pre.reviewPlan");
  const filters = snapshotArray(required(record, "filters", "$pre.filters"), "$pre.filters", 20, 20).map((value, index) => validateFilter(value, `$pre.filters[${index}]`)).sort((left, right) => rawStringCompare(left.filterId, right.filterId));
  const bundles = snapshotArray(required(record, "bundles", "$pre.bundles"), "$pre.bundles", 25, 25).map((value, index) => validateBundle(value, `$pre.bundles[${index}]`)).sort((left, right) => rawStringCompare(left.bundleId, right.bundleId));
  const rawPairs = snapshotArray(required(record, "pairs", "$pre.pairs"), "$pre.pairs", 200, 200).map((value, index) => validatePreAnnotationPair(value, `$pre.pairs[${index}]`));
  const pairs = bundles.flatMap(({ bundleId }) => rawPairs.filter((pair) => pair.bundleId === bundleId).sort((left, right) => left.position - right.position));
  assertCorpusShape(filters, bundles, pairs, "$pre");
  const compiledQueryByFilterId = new Map(extractionManifest.compiledQueries.map(({ filterId, fingerprint }) => [filterId, fingerprint]));
  if (canonicalStageAJson([...compiledQueryByFilterId.keys()].sort(rawStringCompare)) !== canonicalStageAJson(filters.map(({ filterId }) => filterId))) fail("$pre.extractionManifest.compiledQueries", "exact_filter_fingerprint_set_required");
  for (const bundle of bundles) {
    const feed = pairs.filter(({ bundleId }) => bundleId === bundle.bundleId);
    const compiledQueryFingerprint = compiledQueryByFilterId.get(bundle.filterId)!;
    if (bundle.cohort === "production_shaped" && feed.some(({ position, sourceRank }) => position !== sourceRank)) fail("$pre.pairs", "production_must_use_first_eight_required");
    if (bundle.cohort === "challenge" && feed.some(({ sourceRank }, index) => index > 0 && sourceRank <= feed[index - 1].sourceRank)) fail("$pre.pairs", "challenge_source_order_required");
    for (let index = 1; index < feed.length; index += 1) {
      const previous = feed[index - 1];
      const current = feed[index];
      if (previous.postingFirstSeenAt < current.postingFirstSeenAt || (previous.postingFirstSeenAt === current.postingFirstSeenAt && previous.classifierSource.candidateId > current.classifierSource.candidateId)) fail("$pre.pairs", "approved_candidate_order_required");
    }
    for (const pair of feed) {
      if (pair.postingFirstSeenAt < extractionManifest.query.effectiveWindowStart || pair.postingFirstSeenAt >= extractionManifest.query.cutoff) fail("$pre.pairs", "selection_window_required");
      const expectedIdentity = digestStageASourceSnapshotIdentity({
        extractionManifestDigest,
        compiledQueryFingerprint: compiledQueryFingerprint.value,
        candidateId: pair.classifierSource.candidateId,
        contentIdentity: pair.contentIdentity,
        postingFirstSeenAt: pair.postingFirstSeenAt,
        sourceRank: pair.sourceRank,
      });
      if (pair.sourceSnapshotIdentity !== expectedIdentity) fail("$pre.pairs", "source_snapshot_identity_mismatch");
    }
  }
  return Object.freeze({
    schemaVersion: STAGE_A_PRE_ANNOTATION_SCHEMA_VERSION,
    datasetId: evalIdField(record, "datasetId", "$pre.datasetId"),
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationResultDigest: validateDigest(required(record, "calibrationResultDigest", "$pre.calibrationResultDigest"), "$pre.calibrationResultDigest"),
    extractionManifest,
    extractionManifestDigest,
    reviewPlan,
    filters: Object.freeze(filters),
    bundles: Object.freeze(bundles),
    pairs: Object.freeze(pairs),
  });
}

export function digestStageAPreAnnotation(input: unknown): string {
  return domainDigest(STAGE_A_PRE_ANNOTATION_SCHEMA_VERSION, validateStageAPreAnnotation(input));
}

export function digestStageASourceSnapshotIdentity(input: Readonly<{
  extractionManifestDigest: string;
  compiledQueryFingerprint: string;
  candidateId: string;
  contentIdentity: string;
  postingFirstSeenAt: string;
  sourceRank: number;
}>): string {
  return domainDigest("ai-filter-stage-a-source-snapshot-v2", input);
}

function seededOrder(seed: string, id: string): string {
  return createHash("sha256").update(`${seed}\n${id}`, "utf8").digest("hex");
}

export function deriveStageAPromptReviewBundleIds(preInput: unknown): readonly string[] {
  const pre = validateStageAPreAnnotation(preInput);
  const remaining = [...pre.bundles].sort((left, right) => rawStringCompare(seededOrder(pre.reviewPlan.promptReviewSeed, left.bundleId), seededOrder(pre.reviewPlan.promptReviewSeed, right.bundleId)));
  const uncovered = new Set([
    ...COHORTS.map((value) => `cohort:${value}`),
    ...LOCALES.map((value) => `locale:${value}`),
    ...PERSONAS.map((value) => `persona:${value}`),
  ]);
  const selected: StageABundleV2[] = [];
  while (uncovered.size > 0 && selected.length < 12) {
    let bestIndex = -1;
    let bestScore = -1;
    for (let index = 0; index < remaining.length; index += 1) {
      const bundle = remaining[index];
      const score = [`cohort:${bundle.cohort}`, `locale:${bundle.promptLocale}`, `persona:${bundle.persona}`].filter((key) => uncovered.has(key)).length;
      if (score > bestScore) { bestIndex = index; bestScore = score; }
    }
    if (bestIndex < 0 || bestScore === 0) break;
    const [picked] = remaining.splice(bestIndex, 1);
    selected.push(picked);
    uncovered.delete(`cohort:${picked.cohort}`);
    uncovered.delete(`locale:${picked.promptLocale}`);
    uncovered.delete(`persona:${picked.persona}`);
  }
  while (selected.length < 12 && remaining.length > 0) selected.push(remaining.shift()!);
  if (selected.length !== 12 || uncovered.size > 0) fail("$pre.bundles", "prompt_review_coverage_unavailable");
  return Object.freeze(selected.map(({ bundleId }) => bundleId).sort(rawStringCompare));
}

export function validateStageAPromptReviewFeedback(
  input: unknown,
  preInput: unknown,
): StageAPromptReviewFeedbackV2 {
  const pre = validateStageAPreAnnotation(preInput);
  const record = snapshotRecord(input, "$promptFeedback", ["schemaVersion", "reviewId", "reviewerId", "preAnnotationDigest", "approved", "decisions"]);
  requiredLiteral(record, "schemaVersion", "$promptFeedback.schemaVersion", STAGE_A_PROMPT_REVIEW_FEEDBACK_SCHEMA_VERSION);
  const expectedIds = deriveStageAPromptReviewBundleIds(pre);
  const decisions = snapshotArray(required(record, "decisions", "$promptFeedback.decisions"), "$promptFeedback.decisions", 12, 12).map((raw, index) => {
    const itemPath = `$promptFeedback.decisions[${index}]`;
    const decision = snapshotRecord(raw, itemPath, ["bundleId", "decision"]);
    return Object.freeze({
      bundleId: evalIdField(decision, "bundleId", `${itemPath}.bundleId`),
      decision: requiredEnum(decision, "decision", `${itemPath}.decision`, ["keep", "revise", "reject"]),
    });
  }).sort((left, right) => rawStringCompare(left.bundleId, right.bundleId));
  if (canonicalStageAJson(decisions.map(({ bundleId }) => bundleId)) !== canonicalStageAJson(expectedIds)) fail("$promptFeedback.decisions", "derived_prompt_review_set_required");
  const preAnnotationDigest = validateDigest(required(record, "preAnnotationDigest", "$promptFeedback.preAnnotationDigest"), "$promptFeedback.preAnnotationDigest");
  if (preAnnotationDigest !== digestStageAPreAnnotation(pre)) fail("$promptFeedback.preAnnotationDigest", "pre_annotation_digest_mismatch");
  return Object.freeze({
    schemaVersion: STAGE_A_PROMPT_REVIEW_FEEDBACK_SCHEMA_VERSION,
    reviewId: evalIdField(record, "reviewId", "$promptFeedback.reviewId"),
    reviewerId: evalIdField(record, "reviewerId", "$promptFeedback.reviewerId"),
    preAnnotationDigest,
    approved: requiredBoolean(record, "approved", "$promptFeedback.approved"),
    decisions: Object.freeze(decisions),
  });
}

export function digestStageAPromptReviewFeedback(input: unknown, preInput: unknown): string {
  return domainDigest(STAGE_A_PROMPT_REVIEW_FEEDBACK_SCHEMA_VERSION, validateStageAPromptReviewFeedback(input, preInput));
}

export function validateStageAWip(input: unknown): StageAWipV2 {
  const record = snapshotRecord(input, "$", [
    "schemaVersion",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "softQueryNormalizerVersion",
    "calibrationResultDigest",
    "preAnnotationDigest",
    "promptReviewFeedbackDigest",
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
  const rawPairs = snapshotArray(required(record, "pairs", "$.pairs"), "$.pairs", STAGE_A_REQUIRED_PAIRS, STAGE_A_REQUIRED_PAIRS)
    .map((value, index) => validatePair(value, `$.pairs[${index}]`));
  const pairs = bundles.flatMap(({ bundleId }) => rawPairs
    .filter((pair) => pair.bundleId === bundleId)
    .sort((left, right) => left.position - right.position));
  const finalCritic = validateFinalCritic(required(record, "finalCritic", "$.finalCritic"), "$.finalCritic");

  assertCorpusShape(filters, bundles, pairs, "$");
  assertUnique(pairs.flatMap(({ annotations }) => annotations.map(({ annotationId }) => annotationId)), "$.pairs", "unique_annotation_ids_required");
  assertUnique(pairs.flatMap(({ adjudication }) => adjudication ? [adjudication.adjudicationId] : []), "$.pairs", "unique_adjudication_ids_required");
  const bundleById = new Map(bundles.map((bundle) => [bundle.bundleId, bundle]));
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

  const reviewPayload: StageAWipReviewPayloadV2 = Object.freeze({
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: evalIdField(record, "datasetId", "$.datasetId"),
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationResultDigest: validateDigest(required(record, "calibrationResultDigest", "$.calibrationResultDigest"), "$.calibrationResultDigest"),
    preAnnotationDigest: validateDigest(required(record, "preAnnotationDigest", "$.preAnnotationDigest"), "$.preAnnotationDigest"),
    promptReviewFeedbackDigest: validateDigest(required(record, "promptReviewFeedbackDigest", "$.promptReviewFeedbackDigest"), "$.promptReviewFeedbackDigest"),
    filters: Object.freeze(filters),
    bundles: Object.freeze(bundles),
    pairs: Object.freeze(pairs),
  });
  if (finalCritic.reviewedWipDigest !== digestStageAWipReviewPayload(reviewPayload)) {
    fail("$.finalCritic.reviewedWipDigest", "final_critic_review_pin_mismatch");
  }
  return Object.freeze({ ...reviewPayload, finalCritic });
}

export function digestStageAWipReviewPayload(input: unknown): string {
  return domainDigest("ai-filter-stage-a-wip-review-v2", input);
}

function canonicalValue(input: unknown, pathValue = "$"): unknown {
  try {
    if (input === null || typeof input === "string" || typeof input === "boolean") return input;
    if (typeof input === "number") {
      if (!Number.isSafeInteger(input)) fail(pathValue, "canonical_integer_required");
      return input;
    }
    if (typeof input !== "object") fail(pathValue, "canonical_json_value_required");
    if (isProxy(input)) fail(pathValue, "proxy_forbidden");
    if (Array.isArray(input)) {
      const values = snapshotArray(input, pathValue, 0, 100_000);
      return safeArray(values.map((value, index) => canonicalValue(value, `${pathValue}[${index}]`)));
    }
    const prototype = Object.getPrototypeOf(input);
    if (prototype !== Object.prototype && prototype !== null) fail(pathValue, "plain_object_required");
    const descriptors = Object.getOwnPropertyDescriptors(input);
    const output = Object.create(null) as Record<string, unknown>;
    for (const key of Reflect.ownKeys(descriptors).sort((left, right) => rawStringCompare(String(left), String(right)))) {
      if (typeof key !== "string") fail(pathValue, "canonical_json_value_required");
      const descriptor = descriptors[key];
      if (!("value" in descriptor) || !descriptor.enumerable || descriptor.value === undefined) {
        fail(`${pathValue}.${key}`, "canonical_data_property_required");
      }
      output[key] = canonicalValue(descriptor.value, `${pathValue}.${key}`);
    }
    return output;
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail(pathValue, "canonical_value_unreadable");
  }
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

function finalAmbiguity(pair: StageAPairV2): boolean {
  return pair.adjudication?.ambiguity ?? pair.annotations[0].ambiguity;
}

function prePairProjection(pair: StageAPairV2 | StageAPreAnnotationPairV2): StageAPreAnnotationPairV2 {
  return Object.freeze(Object.fromEntries(PAIR_BASE_FIELDS.map((field) => [field, pair[field]]))) as StageAPreAnnotationPairV2;
}

function resolveRoleConfigs(
  calibration: StageACalibrationResultV2,
): Readonly<Record<StageAAgentRole, StageAAgentConfigV2>> {
  const configById = new Map(calibration.candidateConfigs.map((config) => [config.configId, config]));
  return Object.freeze(Object.fromEntries(calibration.selectedConfigs.map((selection) => [
    selection.role,
    configById.get(selection.configId)!,
  ]))) as Readonly<Record<StageAAgentRole, StageAAgentConfigV2>>;
}

function requireConfig(actual: string, expected: StageAAgentConfigV2, pathValue: string): void {
  if (actual !== expected.configId) fail(pathValue, "selected_role_config_required");
}

export function buildStageASilverManifest(
  wipInput: unknown,
  calibrationArtifactInput: unknown,
  expectedCalibrationInputDigest: string,
  calibrationResultInput: unknown,
  expectedCalibrationResultDigest: string,
  preAnnotationInput: unknown,
  expectedPreAnnotationDigest: string,
  promptFeedbackInput: unknown,
  expectedPromptFeedbackDigest: string,
): StageASilverManifestV2 {
  validateDigest(expectedCalibrationResultDigest, "$expectedCalibrationResultDigest");
  validateDigest(expectedPreAnnotationDigest, "$expectedPreAnnotationDigest");
  validateDigest(expectedPromptFeedbackDigest, "$expectedPromptFeedbackDigest");
  const calibration = validateStageACalibrationResult(
    calibrationResultInput,
    calibrationArtifactInput,
    expectedCalibrationInputDigest,
  );
  if (
    digestStageACalibrationResult(
      calibration,
      calibrationArtifactInput,
      expectedCalibrationInputDigest,
    ) !== expectedCalibrationResultDigest
  ) fail("$calibrationResult", "calibration_result_pin_mismatch");
  const pre = validateStageAPreAnnotation(preAnnotationInput);
  if (digestStageAPreAnnotation(pre) !== expectedPreAnnotationDigest) fail("$pre", "pre_annotation_pin_mismatch");
  if (pre.calibrationResultDigest !== expectedCalibrationResultDigest) fail("$pre.calibrationResultDigest", "calibration_result_pin_mismatch");
  const promptFeedback = validateStageAPromptReviewFeedback(promptFeedbackInput, pre);
  if (digestStageAPromptReviewFeedback(promptFeedback, pre) !== expectedPromptFeedbackDigest) fail("$promptFeedback", "prompt_feedback_pin_mismatch");
  if (!promptFeedback.approved || promptFeedback.decisions.some(({ decision }) => decision !== "keep")) fail("$promptFeedback", "prompt_review_approval_required");
  const wip = validateStageAWip(wipInput);
  if (wip.calibrationResultDigest !== expectedCalibrationResultDigest) fail("$.calibrationResultDigest", "calibration_result_pin_mismatch");
  if (wip.preAnnotationDigest !== expectedPreAnnotationDigest) fail("$.preAnnotationDigest", "pre_annotation_pin_mismatch");
  if (wip.promptReviewFeedbackDigest !== expectedPromptFeedbackDigest) fail("$.promptReviewFeedbackDigest", "prompt_feedback_pin_mismatch");
  const wipProjection = {
    datasetId: wip.datasetId,
    classifierInputSchemaVersion: wip.classifierInputSchemaVersion,
    classifierInputNormalizerVersion: wip.classifierInputNormalizerVersion,
    softQueryNormalizerVersion: wip.softQueryNormalizerVersion,
    filters: wip.filters,
    bundles: wip.bundles,
    pairs: wip.pairs.map(prePairProjection),
  };
  const preProjection = {
    datasetId: pre.datasetId,
    classifierInputSchemaVersion: pre.classifierInputSchemaVersion,
    classifierInputNormalizerVersion: pre.classifierInputNormalizerVersion,
    softQueryNormalizerVersion: pre.softQueryNormalizerVersion,
    filters: pre.filters,
    bundles: pre.bundles,
    pairs: pre.pairs,
  };
  if (canonicalStageAJson(wipProjection) !== canonicalStageAJson(preProjection)) fail("$", "pre_annotation_projection_mismatch");
  const configs = resolveRoleConfigs(calibration);
  for (const bundle of wip.bundles) requireConfig(bundle.promptProvenance.configId, configs.prompt_author, "$.bundles.promptProvenance.configId");
  for (const pair of wip.pairs) {
    for (const annotation of pair.annotations) requireConfig(annotation.configId, configs.annotator, "$.pairs.annotations.configId");
    if (pair.adjudication) requireConfig(pair.adjudication.configId, configs.adjudicator, "$.pairs.adjudication.configId");
  }
  requireConfig(wip.finalCritic.configId, configs.final_critic, "$.finalCritic.configId");
  const agentActors = new Set([
    ...wip.bundles.map(({ promptProvenance }) => promptProvenance.authorId),
    ...wip.pairs.flatMap(({ annotations, adjudication }) => [...annotations.map(({ actorId }) => actorId), ...(adjudication ? [adjudication.actorId] : [])]),
    wip.finalCritic.actorId,
  ]);
  if (agentActors.has(calibration.humanReview.reviewerId) || agentActors.has(promptFeedback.reviewerId)) fail("$", "human_agent_role_separation_required");
  const sourceWipDigest = domainDigest(STAGE_A_WIP_SCHEMA_VERSION, wip);
  const pairs = wip.pairs.map((pair) => {
    const classifierInput = normalizeClassifierSource(pair.classifierSource, "$.pairs.classifierSource").payload;
    const adjudicated = pair.adjudication !== null;
    return Object.freeze({
      ...pair,
      classifierInput,
      silverLabel: silverLabel(pair),
      finalAmbiguity: finalAmbiguity(pair),
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
    calibrationResultDigest: wip.calibrationResultDigest,
    preAnnotationDigest: wip.preAnnotationDigest,
    promptReviewFeedbackDigest: wip.promptReviewFeedbackDigest,
    sourceWipDigest,
    extractionManifest: pre.extractionManifest,
    extractionManifestDigest: pre.extractionManifestDigest,
    reviewPlan: pre.reviewPlan,
    filters: wip.filters,
    bundles: wip.bundles,
    pairs: Object.freeze(pairs),
    finalCritic: wip.finalCritic,
  });
}

export function freezeStageASilver(
  wipInput: unknown,
  calibrationArtifactInput: unknown,
  expectedCalibrationInputDigest: string,
  calibrationResultInput: unknown,
  expectedCalibrationResultDigest: string,
  preAnnotationInput: unknown,
  expectedPreAnnotationDigest: string,
  promptFeedbackInput: unknown,
  expectedPromptFeedbackDigest: string,
): StageASilverFreezeV2 {
  const manifest = buildStageASilverManifest(
    wipInput,
    calibrationArtifactInput,
    expectedCalibrationInputDigest,
    calibrationResultInput,
    expectedCalibrationResultDigest,
    preAnnotationInput,
    expectedPreAnnotationDigest,
    promptFeedbackInput,
    expectedPromptFeedbackDigest,
  );
  return Object.freeze({
    schemaVersion: STAGE_A_SILVER_FREEZE_SCHEMA_VERSION,
    manifest,
    silverDigest: domainDigest(STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION, manifest),
  });
}

function basePairFromSilver(input: unknown, pathValue: string): unknown {
  const record = snapshotRecord(input, pathValue, [
    ...PAIR_BASE_FIELDS,
    "classifierSource", "contentIdentity", "annotations", "adjudication", "classifierInput",
    "silverLabel", "finalAmbiguity", "silverProvenance",
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
    ...PAIR_BASE_FIELDS, "annotations", "adjudication",
  ].map((field) => [field, required(record, field, `${pathValue}.${field}`)]));
}

export function validateStageASilverFreeze(
  input: unknown,
  expectedSilverDigest: string,
): StageASilverFreezeV2 {
  validateDigest(expectedSilverDigest, "$expectedSilverDigest");
  const envelope = snapshotRecord(input, "$", ["schemaVersion", "manifest", "silverDigest"]);
  requiredLiteral(envelope, "schemaVersion", "$.schemaVersion", STAGE_A_SILVER_FREEZE_SCHEMA_VERSION);
  const manifestInput = required(envelope, "manifest", "$.manifest");
  const manifest = snapshotRecord(manifestInput, "$.manifest", [
    "schemaVersion", "status", "datasetId", "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion", "softQueryNormalizerVersion", "calibrationResultDigest",
    "preAnnotationDigest", "promptReviewFeedbackDigest", "sourceWipDigest", "extractionManifest",
    "extractionManifestDigest",
    "reviewPlan", "filters", "bundles", "pairs", "finalCritic",
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
    calibrationResultDigest: required(manifest, "calibrationResultDigest", "$.manifest.calibrationResultDigest"),
    preAnnotationDigest: required(manifest, "preAnnotationDigest", "$.manifest.preAnnotationDigest"),
    promptReviewFeedbackDigest: required(manifest, "promptReviewFeedbackDigest", "$.manifest.promptReviewFeedbackDigest"),
    filters: required(manifest, "filters", "$.manifest.filters"),
    bundles: required(manifest, "bundles", "$.manifest.bundles"),
    pairs: pairInputs.map((pair, index) => basePairFromSilver(pair, `$.manifest.pairs[${index}]`)),
    finalCritic: required(manifest, "finalCritic", "$.manifest.finalCritic"),
  };
  const normalizedWip = validateStageAWip(wip);
  const normalizedPairs = pairInputs.map((rawPair, index) => {
    const pairPath = `$.manifest.pairs[${index}]`;
    const rawRecord = snapshotRecord(rawPair, pairPath, [
      ...PAIR_BASE_FIELDS, "annotations", "adjudication", "classifierInput", "silverLabel", "finalAmbiguity", "silverProvenance",
    ]);
    const pair = normalizedWip.pairs[index];
    const frozenInputValue = required(rawRecord, "classifierInput", `${pairPath}.classifierInput`);
    validateFrozenClassifierInput(frozenInputValue, `${pairPath}.classifierInput`);
    const normalizedInput = normalizeClassifierSource(pair.classifierSource, `${pairPath}.classifierSource`).payload;
    if (canonicalStageAJson(frozenInputValue) !== canonicalStageAJson(normalizedInput)) fail(`${pairPath}.classifierInput`, "classifier_input_mismatch");
    const embeddedLabel = validateLabel(required(rawRecord, "silverLabel", `${pairPath}.silverLabel`), `${pairPath}.silverLabel`);
    if (embeddedLabel !== silverLabel(pair)) fail(`${pairPath}.silverLabel`, "silver_label_mismatch");
    const embeddedAmbiguity = requiredBoolean(rawRecord, "finalAmbiguity", `${pairPath}.finalAmbiguity`);
    if (embeddedAmbiguity !== finalAmbiguity(pair)) fail(`${pairPath}.finalAmbiguity`, "final_ambiguity_mismatch");
    const silverProvenance = validateSilverProvenance(required(rawRecord, "silverProvenance", `${pairPath}.silverProvenance`), pair, `${pairPath}.silverProvenance`);
    return Object.freeze({ ...pair, classifierInput: normalizedInput, silverLabel: embeddedLabel, finalAmbiguity: embeddedAmbiguity, silverProvenance });
  });
  const rebuilt: StageASilverManifestV2 = Object.freeze({
    schemaVersion: STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION,
    status: "agent_adjudicated_silver",
    datasetId: normalizedWip.datasetId,
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationResultDigest: normalizedWip.calibrationResultDigest,
    preAnnotationDigest: normalizedWip.preAnnotationDigest,
    promptReviewFeedbackDigest: normalizedWip.promptReviewFeedbackDigest,
    sourceWipDigest: validateDigest(required(manifest, "sourceWipDigest", "$.manifest.sourceWipDigest"), "$.manifest.sourceWipDigest"),
    extractionManifest: validateStageAExtractionManifest(required(manifest, "extractionManifest", "$.manifest.extractionManifest"), "$.manifest.extractionManifest"),
    extractionManifestDigest: validateDigest(required(manifest, "extractionManifestDigest", "$.manifest.extractionManifestDigest"), "$.manifest.extractionManifestDigest"),
    reviewPlan: validateReviewPlan(required(manifest, "reviewPlan", "$.manifest.reviewPlan"), "$.manifest.reviewPlan"),
    filters: normalizedWip.filters,
    bundles: normalizedWip.bundles,
    pairs: Object.freeze(normalizedPairs),
    finalCritic: normalizedWip.finalCritic,
  });
  if (rebuilt.extractionManifestDigest !== digestStageAExtractionManifest(rebuilt.extractionManifest)) fail("$.manifest.extractionManifestDigest", "extraction_manifest_digest_mismatch");
  if (rebuilt.sourceWipDigest !== domainDigest(STAGE_A_WIP_SCHEMA_VERSION, normalizedWip)) fail("$.manifest.sourceWipDigest", "source_wip_digest_mismatch");
  if (canonicalStageAJson(rebuilt) !== canonicalStageAJson(manifestInput)) fail("$.manifest", "derived_manifest_mismatch");
  const silverDigest = validateDigest(required(envelope, "silverDigest", "$.silverDigest"), "$.silverDigest");
  if (silverDigest !== expectedSilverDigest || silverDigest !== domainDigest(STAGE_A_SILVER_MANIFEST_SCHEMA_VERSION, rebuilt)) {
    fail("$.silverDigest", "silver_digest_mismatch");
  }
  return Object.freeze({ schemaVersion: STAGE_A_SILVER_FREEZE_SCHEMA_VERSION, manifest: rebuilt, silverDigest });
}

function auditStratum(pair: StageASilverPairV2): StageAAuditStratum {
  if (pair.evidenceCondition === "policy_boundary" || pair.annotations.some(({ ambiguity }) => ambiguity) || pair.finalAmbiguity) return "adjudicated_ambiguous_policy";
  if (pair.annotations[0].label !== pair.annotations[1].label || pair.annotations[0].ambiguity !== pair.annotations[1].ambiguity) return "adjudicated_disagreement";
  return "agreement_clear";
}

export function deriveStageAHumanAuditPolicy(silverInput: unknown, expectedSilverDigest: string): StageAHumanAuditPolicyV2 {
  const silver = validateStageASilverFreeze(silverInput, expectedSilverDigest);
  const bundleById = new Map(silver.manifest.bundles.map((bundle) => [bundle.bundleId, bundle]));
  const stratified = (candidates: readonly StageASilverPairV2[], count: number, suffix: string): StageASilverPairV2[] => {
    const queues = COHORTS.map((cohort) => candidates
      .filter((pair) => bundleById.get(pair.bundleId)!.cohort === cohort)
      .sort((left, right) => rawStringCompare(
        seededOrder(`${silver.manifest.reviewPlan.auditSeed}:${silver.silverDigest}:${suffix}`, left.pairId),
        seededOrder(`${silver.manifest.reviewPlan.auditSeed}:${silver.silverDigest}:${suffix}`, right.pairId),
      )));
    const output: StageASilverPairV2[] = [];
    const covered = new Set<string>();
    const keys = (pair: StageASilverPairV2): string[] => {
      const bundle = bundleById.get(pair.bundleId)!;
      return [
        `cohort:${bundle.cohort}`,
        `filter:${bundle.filterId}`,
        `persona:${bundle.persona}`,
        `prompt-locale:${bundle.promptLocale}`,
        `pair-locale:${pair.locale}`,
        `evidence:${pair.evidenceCondition}`,
        `label:${pair.silverLabel}`,
      ];
    };
    let turn = 0;
    while (output.length < count && queues.some((queue) => queue.length > 0)) {
      let queue = queues[turn % queues.length];
      if (queue.length === 0) queue = queues[(turn + 1) % queues.length];
      let bestIndex = 0;
      let bestNovelty = -1;
      for (let index = 0; index < queue.length; index += 1) {
        const novelty = keys(queue[index]).filter((key) => !covered.has(key)).length;
        if (novelty > bestNovelty) {
          bestIndex = index;
          bestNovelty = novelty;
        }
      }
      const [picked] = queue.splice(bestIndex, 1);
      output.push(picked);
      for (const key of keys(picked)) covered.add(key);
      turn += 1;
    }
    return output;
  };
  const ambiguousPolicy = silver.manifest.pairs.filter((pair) => auditStratum(pair) === "adjudicated_ambiguous_policy");
  const disagreements = silver.manifest.pairs.filter((pair) => auditStratum(pair) === "adjudicated_disagreement");
  const agreements = silver.manifest.pairs.filter((pair) => auditStratum(pair) === "agreement_clear");
  if (ambiguousPolicy.length > 8 || disagreements.length > 8) fail("$silver.manifest.pairs", "fleet_quality_audit_quota_exceeded");
  const requiredAgreementCount = STAGE_A_HUMAN_AUDIT_PAIRS - ambiguousPolicy.length - disagreements.length;
  if (agreements.length < requiredAgreementCount) fail("$silver.manifest.pairs", "audit_population_insufficient");
  const picked = [
    ...stratified(ambiguousPolicy, ambiguousPolicy.length, "ambiguous-policy"),
    ...stratified(disagreements, disagreements.length, "disagreement"),
    ...stratified(agreements, requiredAgreementCount, "agreement"),
  ];
  return Object.freeze({
    schemaVersion: STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION,
    sourceSilverDigest: silver.silverDigest,
    rule: silver.manifest.reviewPlan.auditRule,
    seed: silver.manifest.reviewPlan.auditSeed,
    auditPairIds: Object.freeze(picked.map(({ pairId }) => pairId).sort(rawStringCompare)),
  });
}

export function validateStageAHumanAuditPolicy(input: unknown, silverInput: unknown, expectedSilverDigest: string): StageAHumanAuditPolicyV2 {
  const record = snapshotRecord(input, "$policy", ["schemaVersion", "sourceSilverDigest", "rule", "seed", "auditPairIds"]);
  requiredLiteral(record, "schemaVersion", "$policy.schemaVersion", STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION);
  const auditPairIds = snapshotArray(required(record, "auditPairIds", "$policy.auditPairIds"), "$policy.auditPairIds", STAGE_A_HUMAN_AUDIT_PAIRS, STAGE_A_HUMAN_AUDIT_PAIRS)
    .map((value, index) => {
      if (typeof value !== "string") fail(`$policy.auditPairIds[${index}]`, "string_required");
      return validateEvalId(value, `$policy.auditPairIds[${index}]`);
    })
    .sort(rawStringCompare);
  assertUnique(auditPairIds, "$policy.auditPairIds", "unique_audit_pair_ids_required");
  const normalized = Object.freeze({
    schemaVersion: STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION,
    sourceSilverDigest: validateDigest(required(record, "sourceSilverDigest", "$policy.sourceSilverDigest"), "$policy.sourceSilverDigest"),
    rule: requiredLiteral(record, "rule", "$policy.rule", "bounded-16-8-8-v2"),
    seed: validateDigest(required(record, "seed", "$policy.seed"), "$policy.seed"),
    auditPairIds: Object.freeze(auditPairIds),
  });
  const expected = deriveStageAHumanAuditPolicy(silverInput, expectedSilverDigest);
  if (canonicalStageAJson(normalized) !== canonicalStageAJson(expected)) fail("$policy", "derived_audit_policy_required");
  return normalized;
}

export function digestStageAHumanAuditPolicy(input: unknown, silverInput: unknown, expectedSilverDigest: string): string {
  return domainDigest(STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION, validateStageAHumanAuditPolicy(input, silverInput, expectedSilverDigest));
}

function validateHumanFeedback(input: unknown): StageAHumanFeedbackV2 {
  const record = snapshotRecord(input, "$feedback", [
    "schemaVersion", "feedbackId", "reviewerId", "sourceSilverDigest",
    "auditPolicyDigest", "approved", "decisions",
  ]);
  requiredLiteral(record, "schemaVersion", "$feedback.schemaVersion", STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION);
  const decisions = snapshotArray(required(record, "decisions", "$feedback.decisions"), "$feedback.decisions", STAGE_A_HUMAN_AUDIT_PAIRS, STAGE_A_HUMAN_AUDIT_PAIRS)
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
  policyInput: unknown,
  expectedPolicyDigest: string,
  feedbackInput: unknown,
): StageAGoldFreezeV2 {
  const silver = validateStageASilverFreeze(silverInput, expectedSilverDigest);
  const policy = validateStageAHumanAuditPolicy(policyInput, silver, expectedSilverDigest);
  validateDigest(expectedPolicyDigest, "$expectedPolicyDigest");
  if (policy.sourceSilverDigest !== silver.silverDigest || digestStageAHumanAuditPolicy(policy, silver, expectedSilverDigest) !== expectedPolicyDigest) {
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
        source: humanJudgment !== undefined
          ? "human_reviewed" as const
          : pair.silverProvenance.method === "adjudication"
            ? "agent_adjudicated" as const
            : "agent_agreed" as const,
        sourcePairId: pair.pairId,
        humanFeedbackId: humanJudgment === undefined ? null : feedback.feedbackId,
        corrected,
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
    calibrationResultDigest: silver.manifest.calibrationResultDigest,
    preAnnotationDigest: silver.manifest.preAnnotationDigest,
    promptReviewFeedbackDigest: silver.manifest.promptReviewFeedbackDigest,
    extractionManifest: silver.manifest.extractionManifest,
    extractionManifestDigest: silver.manifest.extractionManifestDigest,
    reviewPlan: silver.manifest.reviewPlan,
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
  calibrationArtifactInput: unknown,
  expectedCalibrationInputDigest: string,
  calibrationResultInput: unknown,
  expectedCalibrationResultDigest: string,
  preAnnotationInput: unknown,
  expectedPreAnnotationDigest: string,
  promptFeedbackInput: unknown,
  expectedPromptFeedbackDigest: string,
): Promise<Readonly<{ silverDigest: string }>> {
  const frozen = freezeStageASilver(
    wipInput,
    calibrationArtifactInput,
    expectedCalibrationInputDigest,
    calibrationResultInput,
    expectedCalibrationResultDigest,
    preAnnotationInput,
    expectedPreAnnotationDigest,
    promptFeedbackInput,
    expectedPromptFeedbackDigest,
  );
  await publishPrivateFile(relativePath, canonicalStageAJson(frozen));
  return Object.freeze({ silverDigest: frozen.silverDigest });
}

export async function writeStageAGoldFreezeFile(
  relativePath: string,
  silverInput: unknown,
  expectedSilverDigest: string,
  policyInput: unknown,
  expectedPolicyDigest: string,
  feedbackInput: unknown,
): Promise<Readonly<{ goldDigest: string }>> {
  const frozen = promoteStageAGold(
    silverInput,
    expectedSilverDigest,
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
    ...PAIR_BASE_FIELDS, "annotations", "adjudication", "classifierInput",
    "silverLabel", "finalAmbiguity", "silverProvenance", "goldLabel", "goldProvenance",
  ]);
  const baseInput = Object.fromEntries([
    ...PAIR_BASE_FIELDS, "annotations", "adjudication",
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
  const embeddedFinalAmbiguity = requiredBoolean(record, "finalAmbiguity", `${pathValue}.finalAmbiguity`);
  if (embeddedFinalAmbiguity !== finalAmbiguity(pair)) fail(`${pathValue}.finalAmbiguity`, "final_ambiguity_mismatch");
  const silverProvenance = validateSilverProvenance(required(record, "silverProvenance", `${pathValue}.silverProvenance`), pair, `${pathValue}.silverProvenance`);
  const goldLabel = validateLabel(required(record, "goldLabel", `${pathValue}.goldLabel`), `${pathValue}.goldLabel`);
  const provenanceRecord = snapshotRecord(required(record, "goldProvenance", `${pathValue}.goldProvenance`), `${pathValue}.goldProvenance`, ["source", "sourcePairId", "humanFeedbackId", "corrected"]);
  const source = requiredEnum(provenanceRecord, "source", `${pathValue}.goldProvenance.source`, ["agent_agreed", "agent_adjudicated", "human_reviewed"]);
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
  const corrected = requiredBoolean(provenanceRecord, "corrected", `${pathValue}.goldProvenance.corrected`);
  const expectedCorrected = goldLabel !== embeddedSilverLabel;
  const expectedSource = rowFeedbackId !== null
    ? "human_reviewed"
    : silverProvenance.method === "adjudication"
      ? "agent_adjudicated"
      : "agent_agreed";
  if (corrected !== expectedCorrected || source !== expectedSource || (corrected && rowFeedbackId === null)) {
    fail(`${pathValue}.goldProvenance`, "gold_provenance_mismatch");
  }
  return Object.freeze({
    ...pair,
    classifierInput: frozenInput,
    silverLabel: embeddedSilverLabel,
    finalAmbiguity: embeddedFinalAmbiguity,
    silverProvenance,
    goldLabel,
    goldProvenance: Object.freeze({ source, sourcePairId, humanFeedbackId: rowFeedbackId, corrected }),
  });
}

function validateGoldFreeze(
  input: unknown,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationResultDigest: string,
): StageAGoldFreezeV2 {
  validateDigest(expectedGoldDigest, "$expectedGoldDigest");
  validateDigest(expectedSilverDigest, "$expectedSilverDigest");
  validateDigest(expectedAuditPolicyDigest, "$expectedAuditPolicyDigest");
  validateDigest(expectedCalibrationResultDigest, "$expectedCalibrationResultDigest");
  const envelope = snapshotRecord(input, "$", ["schemaVersion", "manifest", "goldDigest"]);
  requiredLiteral(envelope, "schemaVersion", "$.schemaVersion", STAGE_A_GOLD_FREEZE_SCHEMA_VERSION);
  const manifestInput = required(envelope, "manifest", "$.manifest");
  const record = snapshotRecord(manifestInput, "$.manifest", [
    "schemaVersion", "status", "sourceSilverDigest", "auditPolicyDigest",
    "humanFeedbackDigest", "humanFeedbackId", "datasetId", "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion", "softQueryNormalizerVersion", "calibrationResultDigest",
    "preAnnotationDigest", "promptReviewFeedbackDigest", "extractionManifest",
    "extractionManifestDigest", "reviewPlan",
    "filters", "bundles", "pairs", "finalCritic",
  ]);
  requiredLiteral(record, "schemaVersion", "$.manifest.schemaVersion", STAGE_A_GOLD_MANIFEST_SCHEMA_VERSION);
  requiredLiteral(record, "status", "$.manifest.status", "human_audited_gold");
  requiredLiteral(record, "classifierInputSchemaVersion", "$.manifest.classifierInputSchemaVersion", CLASSIFIER_INPUT_SCHEMA_VERSION);
  requiredLiteral(record, "classifierInputNormalizerVersion", "$.manifest.classifierInputNormalizerVersion", CLASSIFIER_INPUT_NORMALIZER_VERSION);
  requiredLiteral(record, "softQueryNormalizerVersion", "$.manifest.softQueryNormalizerVersion", AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION);
  const sourceSilverDigest = validateDigest(required(record, "sourceSilverDigest", "$.manifest.sourceSilverDigest"), "$.manifest.sourceSilverDigest");
  const auditPolicyDigest = validateDigest(required(record, "auditPolicyDigest", "$.manifest.auditPolicyDigest"), "$.manifest.auditPolicyDigest");
  const calibrationResultDigest = validateDigest(required(record, "calibrationResultDigest", "$.manifest.calibrationResultDigest"), "$.manifest.calibrationResultDigest");
  if (sourceSilverDigest !== expectedSilverDigest || auditPolicyDigest !== expectedAuditPolicyDigest || calibrationResultDigest !== expectedCalibrationResultDigest) {
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
    calibrationResultDigest,
    preAnnotationDigest: required(record, "preAnnotationDigest", "$.manifest.preAnnotationDigest"),
    promptReviewFeedbackDigest: required(record, "promptReviewFeedbackDigest", "$.manifest.promptReviewFeedbackDigest"),
    filters: filtersInput,
    bundles: bundlesInput,
    pairs: pairs.map(({ classifierInput: _classifierInput, silverLabel: _silverLabel, finalAmbiguity: _finalAmbiguity, silverProvenance: _silverProvenance, goldLabel: _goldLabel, goldProvenance: _goldProvenance, ...pair }) => pair),
    finalCritic: required(record, "finalCritic", "$.manifest.finalCritic"),
  });
  const auditedCount = pairs.filter(({ goldProvenance }) => goldProvenance.humanFeedbackId !== null).length;
  if (auditedCount !== STAGE_A_HUMAN_AUDIT_PAIRS) fail("$.manifest.pairs", "complete_human_audit_required");
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
    calibrationResultDigest,
    preAnnotationDigest: validateDigest(required(record, "preAnnotationDigest", "$.manifest.preAnnotationDigest"), "$.manifest.preAnnotationDigest"),
    promptReviewFeedbackDigest: validateDigest(required(record, "promptReviewFeedbackDigest", "$.manifest.promptReviewFeedbackDigest"), "$.manifest.promptReviewFeedbackDigest"),
    extractionManifest: validateStageAExtractionManifest(required(record, "extractionManifest", "$.manifest.extractionManifest"), "$.manifest.extractionManifest"),
    extractionManifestDigest: validateDigest(required(record, "extractionManifestDigest", "$.manifest.extractionManifestDigest"), "$.manifest.extractionManifestDigest"),
    reviewPlan: validateReviewPlan(required(record, "reviewPlan", "$.manifest.reviewPlan"), "$.manifest.reviewPlan"),
    filters: wip.filters,
    bundles: wip.bundles,
    pairs: Object.freeze(wip.pairs.map(({ pairId }) => pairs.find((pair) => pair.pairId === pairId)!)),
    finalCritic: wip.finalCritic,
  });
  if (manifest.extractionManifestDigest !== digestStageAExtractionManifest(manifest.extractionManifest)) fail("$.manifest.extractionManifestDigest", "extraction_manifest_digest_mismatch");
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
  expectedCalibrationResultDigest: string,
): Promise<StageAGoldFreezeV2> {
  return validateGoldFreeze(
    await readPrivateJson(relativePath),
    expectedGoldDigest,
    expectedSilverDigest,
    expectedAuditPolicyDigest,
    expectedCalibrationResultDigest,
  );
}

export async function loadStageATargetInputs(
  relativePath: string,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationResultDigest: string,
): Promise<readonly StageATargetInputV2[]> {
  const frozen = await readValidatedGoldFreeze(relativePath, expectedGoldDigest, expectedSilverDigest, expectedAuditPolicyDigest, expectedCalibrationResultDigest);
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
  expectedCalibrationResultDigest: string,
): Promise<readonly StageAScoringLabelV2[]> {
  const frozen = await readValidatedGoldFreeze(relativePath, expectedGoldDigest, expectedSilverDigest, expectedAuditPolicyDigest, expectedCalibrationResultDigest);
  return deepFreeze(safeArray(frozen.manifest.pairs.map(({ pairId, goldLabel }) => frozenNullPrototypeRecord({ pairId, label: goldLabel }))));
}

export async function reportStageAGoldFreeze(
  relativePath: string,
  expectedGoldDigest: string,
  expectedSilverDigest: string,
  expectedAuditPolicyDigest: string,
  expectedCalibrationResultDigest: string,
): Promise<StageAReportV2> {
  const frozen = await readValidatedGoldFreeze(relativePath, expectedGoldDigest, expectedSilverDigest, expectedAuditPolicyDigest, expectedCalibrationResultDigest);
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
  silverInput: unknown,
  expectedSilverDigest: string,
): Promise<StageAHumanAuditPolicyV2> {
  return validateStageAHumanAuditPolicy(await readPrivateJson(relativePath), silverInput, expectedSilverDigest);
}
