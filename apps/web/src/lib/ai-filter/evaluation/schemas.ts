import {
  CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT,
  CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT,
  CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT,
  CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
} from "../classifier-input";
import { AI_FILTER_QUERY_MAX_LENGTH, AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION } from "../contract";

const idPattern = "^eval-[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$";
const digestPattern = "^[a-f0-9]{64}$";
const candidateIdPattern = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$";
const tokenPattern = "^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$";
const instantPattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.000Z$";
const canonicalQueryPattern = "^(?![\\s\\S]*(?:\\p{Cc}|\\p{Default_Ignorable_Code_Point}))(?![\\s\\S]*[^\\S ])(?:\\S|\\S(?:[^\\s]| (?! ))*\\S)$";
const enumString = (values: readonly string[]) => ({ type: "string", enum: values });
const digest = { type: "string", pattern: digestPattern } as const;
const evalId = { type: "string", pattern: idPattern } as const;

const classifierSourceSchema = {
  type: "object", additionalProperties: false,
  required: ["candidateId", "title", "companyName", "descriptionHtml", "selectedDescriptionLocale"],
  properties: {
    candidateId: { type: "string", pattern: candidateIdPattern },
    title: { type: "string", maxLength: CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT },
    companyName: { type: "string", maxLength: CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT },
    descriptionHtml: { type: "string", maxLength: CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT },
    selectedDescriptionLocale: { type: "string", maxLength: CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT },
  },
} as const;

const classifierInputSchema = {
  type: "object", additionalProperties: false,
  required: ["schemaVersion", "candidateId", "title", "companyName", "descriptionText"],
  properties: {
    schemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
    candidateId: { type: "string", pattern: candidateIdPattern },
    title: { type: "string", minLength: 1, maxLength: CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT },
    companyName: { type: "string", minLength: 1, maxLength: CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT },
    descriptionText: { type: "string", minLength: 1, maxLength: CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT },
  },
} as const;

const generalizedContextSchema = {
  type: "object", additionalProperties: false,
  required: ["companyScope", "locationScope", "occupationScope", "keywordScope", "seniorityScope", "technologyScope", "workModeScope", "employmentTypeScope", "compensationScope", "experienceScope", "locale"],
  properties: {
    companyScope: enumString(["any", "selected"]),
    locationScope: enumString(["none", "single", "multiple", "global"]),
    occupationScope: enumString(["none", "single", "multiple"]),
    keywordScope: enumString(["none", "single", "multiple"]),
    seniorityScope: enumString(["none", "single", "multiple"]),
    technologyScope: enumString(["none", "single", "multiple"]),
    workModeScope: enumString(["none", "single", "multiple"]),
    employmentTypeScope: enumString(["none", "single", "multiple"]),
    compensationScope: enumString(["none", "minimum", "maximum", "range"]),
    experienceScope: enumString(["none", "minimum", "maximum", "range"]),
    locale: enumString(["de", "en", "fr", "it", "other"]),
  },
} as const;

const promptProvenanceSchema = {
  type: "object", additionalProperties: false,
  required: ["origin", "authorId", "configId"],
  properties: { origin: { const: "agent_synthetic" }, authorId: evalId, configId: evalId },
} as const;

const annotationSchema = {
  type: "object", additionalProperties: false,
  required: ["annotationId", "actorId", "configId", "label", "ambiguity", "rationaleCode", "evidenceRefs"],
  properties: {
    annotationId: evalId, actorId: evalId, configId: evalId,
    label: enumString(["accept", "reject"]), ambiguity: { type: "boolean" },
    rationaleCode: enumString(["direct_evidence", "missing_evidence", "contradiction", "policy_interpretation", "prompt_injection_ignored"]),
    evidenceRefs: { type: "array", minItems: 1, maxItems: 4, uniqueItems: true, items: enumString(["title", "company_name", "description_text", "absence_in_snapshot"]) },
  },
} as const;

const adjudicationSchema = {
  type: "object", additionalProperties: false,
  required: ["adjudicationId", "actorId", "configId", "annotationIds", "label", "ambiguity", "rationaleCode"],
  properties: {
    adjudicationId: evalId, actorId: evalId, configId: evalId,
    annotationIds: { type: "array", minItems: 2, maxItems: 2, uniqueItems: true, items: evalId },
    label: enumString(["accept", "reject"]), ambiguity: { type: "boolean" },
    rationaleCode: enumString(["direct_evidence", "missing_evidence", "contradiction", "policy_interpretation", "prompt_injection_ignored"]),
  },
} as const;

const pairBaseProperties = {
  schemaVersion: { const: "ai-filter-stage-a-pair-v2" }, pairId: evalId, bundleId: evalId,
  position: { type: "integer", minimum: 0, maximum: 7 },
  sourceRank: { type: "integer", minimum: 0, maximum: 1_000_000 },
  postingFirstSeenAt: { type: "string", pattern: instantPattern }, sourceSnapshotIdentity: digest,
  locale: enumString(["de", "en", "fr", "it"]),
  evidenceCondition: enumString(["direct_support", "direct_conflict", "insufficient_evidence", "policy_boundary", "prompt_injection"]),
  classifierSource: classifierSourceSchema, contentIdentity: digest,
} as const;
const pairBaseRequired = ["schemaVersion", "pairId", "bundleId", "position", "sourceRank", "postingFirstSeenAt", "sourceSnapshotIdentity", "locale", "evidenceCondition", "classifierSource", "contentIdentity"] as const;

export const STAGE_A_FILTER_V2_SCHEMA = {
  $id: "ai-filter-stage-a-filter-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", "filterId", "source", "generalizedContext"],
  properties: { schemaVersion: { const: "ai-filter-stage-a-filter-v2" }, filterId: evalId, source: { const: "production_deidentified" }, generalizedContext: generalizedContextSchema },
} as const;

export const STAGE_A_BUNDLE_V2_SCHEMA = {
  $id: "ai-filter-stage-a-bundle-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", "bundleId", "filterId", "cohort", "persona", "promptLocale", "softQuery", "promptProvenance"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-bundle-v2" }, bundleId: evalId, filterId: evalId,
    cohort: enumString(["production_shaped", "challenge"]),
    persona: enumString(["lazy", "verbose", "misunderstood_purpose", "precise", "vague", "contradictory", "multilingual"]),
    promptLocale: enumString(["de", "en", "fr", "it"]),
    softQuery: { type: "string", minLength: 1, maxLength: AI_FILTER_QUERY_MAX_LENGTH, pattern: canonicalQueryPattern },
    promptProvenance: promptProvenanceSchema,
  },
} as const;

export const STAGE_A_PRE_ANNOTATION_PAIR_V2_SCHEMA = {
  $id: "ai-filter-stage-a-pre-annotation-pair-v2", type: "object", additionalProperties: false,
  required: pairBaseRequired, properties: pairBaseProperties,
} as const;

export const STAGE_A_PAIR_V2_SCHEMA = {
  $id: "ai-filter-stage-a-pair-v2", type: "object", additionalProperties: false,
  required: [...pairBaseRequired, "annotations", "adjudication"],
  properties: {
    ...pairBaseProperties,
    annotations: { type: "array", minItems: 2, maxItems: 2, items: annotationSchema },
    adjudication: { anyOf: [{ type: "null" }, adjudicationSchema] },
  },
} as const;

const sourcePinSchema = (sourcePath: string, exportName: string) => ({
  type: "object", additionalProperties: false,
  required: ["sourcePath", "exportName", "sourceDigest"],
  properties: { sourcePath: { const: sourcePath }, exportName: { const: exportName }, sourceDigest: digest },
} as const);

const normalizerPinSchema = (sourcePath: string, exportName: string, version: string | number) => ({
  type: "object", additionalProperties: false,
  required: ["sourcePath", "exportName", "sourceDigest", "version", "fallbackPolicyDigest", "truncationPolicyDigest"],
  properties: {
    sourcePath: { const: sourcePath }, exportName: { const: exportName }, sourceDigest: digest,
    version: { const: version }, fallbackPolicyDigest: digest, truncationPolicyDigest: digest,
  },
} as const);

const extractionManifestSchema = {
  type: "object", additionalProperties: false,
  required: ["schemaVersion", "repositoryCommit", "af1ContractVersion", "typesense", "compiler", "reader", "dependencyLockDigest", "compiledQueries", "query", "classifierNormalizer", "softQueryNormalizer"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-extraction-manifest-v2" },
    repositoryCommit: { type: "string", pattern: "^[a-f0-9]{40}$" },
    af1ContractVersion: { const: 1 },
    typesense: { type: "object", additionalProperties: false, required: ["collectionAlias", "resolvedCollection", "snapshotDigest"], properties: {
      collectionAlias: { const: "job_posting" }, resolvedCollection: { type: "string", pattern: "^job_posting_v[1-9]\\d*$" }, snapshotDigest: digest,
    } },
    compiler: sourcePinSchema("apps/web/src/lib/search/watchlist-candidate-query.ts", "buildWatchlistCandidateSearchParams"),
    reader: sourcePinSchema("apps/web/src/lib/services/watchlist-matcher.ts", "readWatchlistCandidates"),
    dependencyLockDigest: digest,
    compiledQueries: { type: "array", minItems: 20, maxItems: 20, items: { type: "object", additionalProperties: false, required: ["filterId", "fingerprint"], properties: {
      filterId: evalId, fingerprint: { type: "object", additionalProperties: false, required: ["scheme", "value"], properties: { scheme: { const: "hmac-sha256-v1" }, value: digest } },
    } } },
    query: { type: "object", additionalProperties: false, required: ["templateDigest", "order", "pageSize", "requestedStrictLowerBound", "effectiveWindowStart", "cutoff", "requestedBoundary", "effectiveBoundary", "productionSelection", "challengeSelection"], properties: {
      templateDigest: digest, order: { const: "first_seen_at_desc_candidate_id_asc" }, pageSize: { const: 8 },
      requestedStrictLowerBound: { type: "string", pattern: instantPattern }, effectiveWindowStart: { type: "string", pattern: instantPattern }, cutoff: { type: "string", pattern: instantPattern },
      requestedBoundary: { const: "(requestedStrictLowerBound,cutoff)" }, effectiveBoundary: { const: "[effectiveWindowStart,cutoff)" }, productionSelection: { const: "first_eight" }, challengeSelection: { const: "frozen_source_rank" },
    } },
    classifierNormalizer: normalizerPinSchema("apps/web/src/lib/ai-filter/classifier-input.ts", "normalizeClassifierInputV1", CLASSIFIER_INPUT_NORMALIZER_VERSION),
    softQueryNormalizer: normalizerPinSchema("apps/web/src/lib/ai-filter/contract.ts", "normalizeAiFilterSoftQueryV1", AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION),
  },
} as const;

const reviewPlanSchema = {
  type: "object", additionalProperties: false,
  required: ["promptReviewSeed", "auditSeed", "auditRule", "auditSize"],
  properties: { promptReviewSeed: digest, auditSeed: digest, auditRule: { const: "bounded-16-8-8-v2" }, auditSize: { const: 32 } },
} as const;

const coreArtifactProperties = {
  datasetId: evalId,
  classifierInputSchemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
  classifierInputNormalizerVersion: { const: CLASSIFIER_INPUT_NORMALIZER_VERSION },
  softQueryNormalizerVersion: { const: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION },
  calibrationResultDigest: digest,
  filters: { type: "array", minItems: 20, maxItems: 20, items: { $ref: "ai-filter-stage-a-filter-v2" } },
  bundles: { type: "array", minItems: 25, maxItems: 25, items: { $ref: "ai-filter-stage-a-bundle-v2" } },
} as const;
const coreRequired = ["datasetId", "classifierInputSchemaVersion", "classifierInputNormalizerVersion", "softQueryNormalizerVersion", "calibrationResultDigest", "filters", "bundles"] as const;

export const STAGE_A_CALIBRATION_ARTIFACT_V2_SCHEMA = {
  $id: "ai-filter-stage-a-calibration-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", "calibrationId", "examples"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-calibration-v2" },
    calibrationId: evalId,
    examples: { type: "array", minItems: 24, maxItems: 32, items: {
      type: "object", additionalProperties: false,
      required: ["calibrationExampleId", "softQuery", "classifierSource", "contentIdentity"],
      properties: {
        calibrationExampleId: evalId,
        softQuery: { type: "string", minLength: 1, maxLength: AI_FILTER_QUERY_MAX_LENGTH, pattern: canonicalQueryPattern },
        classifierSource: classifierSourceSchema,
        contentIdentity: digest,
      },
    } },
  },
} as const;

export const STAGE_A_CALIBRATION_RESULT_V2_SCHEMA = {
  $id: "ai-filter-stage-a-calibration-result-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", "calibrationId", "calibrationInputDigest", "humanReview", "candidateConfigs", "trials", "selectedConfigs"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-calibration-result-v2" }, calibrationId: evalId, calibrationInputDigest: digest,
    humanReview: { type: "object", additionalProperties: false, required: ["reviewId", "reviewerId", "approved", "decisions"], properties: {
      reviewId: evalId, reviewerId: evalId, approved: { const: true }, decisions: { type: "array", minItems: 24, maxItems: 32, items: { type: "object", additionalProperties: false, required: ["calibrationExampleId", "judgment"], properties: { calibrationExampleId: evalId, judgment: enumString(["accept", "reject", "unclear"]) } } },
    } },
    candidateConfigs: { type: "array", minItems: 8, maxItems: 16, items: { type: "object", additionalProperties: false, required: ["configId", "role", "model", "modelVersion", "reasoningEffort", "taskPromptDigest"], properties: {
      configId: evalId, role: enumString(["prompt_author", "annotator", "adjudicator", "final_critic"]), model: { type: "string", pattern: tokenPattern }, modelVersion: { type: "string", pattern: tokenPattern }, reasoningEffort: enumString(["low", "medium", "high", "xhigh", "max", "ultra"]), taskPromptDigest: digest,
    } } },
    trials: { type: "array", minItems: 8, maxItems: 16, items: { type: "object", additionalProperties: false, required: ["trialId", "configId", "role", "suiteKind", "suiteInputDigest", "outputDigest", "sampleCount", "blindedScore", "spotCheck"], properties: {
      trialId: evalId, configId: evalId, role: enumString(["prompt_author", "annotator", "adjudicator", "final_critic"]), suiteKind: enumString(["same_eight_disposable_feeds", "resolved_human_ground_truth", "seeded_conflicts", "seeded_defects"]), suiteInputDigest: digest, outputDigest: digest, sampleCount: { type: "integer", minimum: 1, maximum: 10_000 }, blindedScore: { type: "integer", minimum: 0, maximum: 10_000 },
      spotCheck: { type: "object", additionalProperties: false, required: ["disposition", "failureCodes"], properties: { disposition: enumString(["pass", "fail"]), failureCodes: { type: "array", maxItems: 16, uniqueItems: true, items: { type: "string", pattern: tokenPattern } } }, allOf: [
        { if: { type: "object", required: ["disposition"], properties: { disposition: { const: "pass" } } }, then: { type: "object", properties: { failureCodes: { type: "array", maxItems: 0 } } } },
        { if: { type: "object", required: ["disposition"], properties: { disposition: { const: "fail" } } }, then: { type: "object", properties: { failureCodes: { type: "array", minItems: 1 } } } },
      ] },
    }, allOf: [
      { if: { type: "object", required: ["role"], properties: { role: { const: "prompt_author" } } }, then: { type: "object", properties: { suiteKind: { const: "same_eight_disposable_feeds" }, sampleCount: { const: 8 } } } },
      { if: { type: "object", required: ["role"], properties: { role: { const: "annotator" } } }, then: { type: "object", properties: { suiteKind: { const: "resolved_human_ground_truth" } } } },
      { if: { type: "object", required: ["role"], properties: { role: { const: "adjudicator" } } }, then: { type: "object", properties: { suiteKind: { const: "seeded_conflicts" }, sampleCount: { type: "integer", minimum: 8 } } } },
      { if: { type: "object", required: ["role"], properties: { role: { const: "final_critic" } } }, then: { type: "object", properties: { suiteKind: { const: "seeded_defects" }, sampleCount: { type: "integer", minimum: 8 } } } },
    ] } },
    selectedConfigs: { type: "array", minItems: 4, maxItems: 4, items: { type: "object", additionalProperties: false, required: ["role", "configId", "selectionDisposition"], properties: { role: enumString(["prompt_author", "annotator", "adjudicator", "final_critic"]), configId: evalId, selectionDisposition: { const: "approved" } } } },
  },
} as const;

export const STAGE_A_PRE_ANNOTATION_V2_SCHEMA = {
  $id: "ai-filter-stage-a-pre-annotation-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", ...coreRequired, "extractionManifest", "extractionManifestDigest", "reviewPlan", "pairs"],
  properties: { schemaVersion: { const: "ai-filter-stage-a-pre-annotation-v2" }, ...coreArtifactProperties, extractionManifest: extractionManifestSchema, extractionManifestDigest: digest, reviewPlan: reviewPlanSchema, pairs: { type: "array", minItems: 200, maxItems: 200, items: { $ref: "ai-filter-stage-a-pre-annotation-pair-v2" } } },
} as const;

export const STAGE_A_PROMPT_REVIEW_FEEDBACK_V2_SCHEMA = {
  $id: "ai-filter-stage-a-prompt-review-feedback-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", "reviewId", "reviewerId", "preAnnotationDigest", "approved", "decisions"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-prompt-review-feedback-v2" }, reviewId: evalId, reviewerId: evalId, preAnnotationDigest: digest, approved: { type: "boolean" },
    decisions: { type: "array", minItems: 12, maxItems: 12, items: { type: "object", additionalProperties: false, required: ["bundleId", "decision"], properties: { bundleId: evalId, decision: enumString(["keep", "revise", "reject"]) } } },
  },
} as const;

const finalCriticSchema = { type: "object", additionalProperties: false, required: ["reviewId", "actorId", "configId", "reviewedWipDigest", "approved"], properties: { reviewId: evalId, actorId: evalId, configId: evalId, reviewedWipDigest: digest, approved: { const: true } } } as const;

export const STAGE_A_WIP_V2_SCHEMA = {
  $id: "ai-filter-stage-a-wip-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", ...coreRequired, "preAnnotationDigest", "promptReviewFeedbackDigest", "pairs", "finalCritic"],
  properties: { schemaVersion: { const: "ai-filter-stage-a-wip-v2" }, ...coreArtifactProperties, preAnnotationDigest: digest, promptReviewFeedbackDigest: digest, pairs: { type: "array", minItems: 200, maxItems: 200, items: { $ref: "ai-filter-stage-a-pair-v2" } }, finalCritic: finalCriticSchema },
} as const;

const silverProvenanceSchema = { type: "object", additionalProperties: false, required: ["method", "annotationIds", "adjudicationId"], properties: { method: enumString(["agreement", "adjudication"]), annotationIds: { type: "array", minItems: 2, maxItems: 2, uniqueItems: true, items: evalId }, adjudicationId: { anyOf: [{ type: "null" }, evalId] } } } as const;
const silverPairSchema = { type: "object", additionalProperties: false, required: [...STAGE_A_PAIR_V2_SCHEMA.required, "classifierInput", "silverLabel", "finalAmbiguity", "silverProvenance"], properties: { ...STAGE_A_PAIR_V2_SCHEMA.properties, classifierInput: classifierInputSchema, silverLabel: enumString(["accept", "reject"]), finalAmbiguity: { type: "boolean" }, silverProvenance: silverProvenanceSchema } } as const;

export const STAGE_A_SILVER_MANIFEST_V2_SCHEMA = {
  $id: "ai-filter-stage-a-silver-manifest-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", "status", ...coreRequired, "preAnnotationDigest", "promptReviewFeedbackDigest", "sourceWipDigest", "extractionManifest", "extractionManifestDigest", "reviewPlan", "pairs", "finalCritic"],
  properties: { schemaVersion: { const: "ai-filter-stage-a-silver-manifest-v2" }, status: { const: "agent_adjudicated_silver" }, ...coreArtifactProperties, preAnnotationDigest: digest, promptReviewFeedbackDigest: digest, sourceWipDigest: digest, extractionManifest: extractionManifestSchema, extractionManifestDigest: digest, reviewPlan: reviewPlanSchema, pairs: { type: "array", minItems: 200, maxItems: 200, items: silverPairSchema }, finalCritic: finalCriticSchema },
} as const;

export const STAGE_A_SILVER_FREEZE_V2_SCHEMA = { $id: "ai-filter-stage-a-silver-freeze-v2", type: "object", additionalProperties: false, required: ["schemaVersion", "manifest", "silverDigest"], properties: { schemaVersion: { const: "ai-filter-stage-a-silver-freeze-v2" }, manifest: { $ref: "ai-filter-stage-a-silver-manifest-v2" }, silverDigest: digest } } as const;

export const STAGE_A_HUMAN_AUDIT_POLICY_V2_SCHEMA = { $id: "ai-filter-stage-a-human-audit-policy-v2", type: "object", additionalProperties: false, required: ["schemaVersion", "sourceSilverDigest", "rule", "seed", "auditPairIds"], properties: { schemaVersion: { const: "ai-filter-stage-a-human-audit-policy-v2" }, sourceSilverDigest: digest, rule: { const: "bounded-16-8-8-v2" }, seed: digest, auditPairIds: { type: "array", minItems: 32, maxItems: 32, uniqueItems: true, items: evalId } } } as const;

export const STAGE_A_HUMAN_FEEDBACK_V2_SCHEMA = { $id: "ai-filter-stage-a-human-feedback-v2", type: "object", additionalProperties: false, required: ["schemaVersion", "feedbackId", "reviewerId", "sourceSilverDigest", "auditPolicyDigest", "approved", "decisions"], properties: { schemaVersion: { const: "ai-filter-stage-a-human-feedback-v2" }, feedbackId: evalId, reviewerId: evalId, sourceSilverDigest: digest, auditPolicyDigest: digest, approved: { type: "boolean" }, decisions: { type: "array", minItems: 32, maxItems: 32, items: { type: "object", additionalProperties: false, required: ["pairId", "judgment"], properties: { pairId: evalId, judgment: enumString(["accept", "reject", "unclear"]) } } } } } as const;

const goldProvenanceSchema = { type: "object", additionalProperties: false, required: ["source", "sourcePairId", "humanFeedbackId", "corrected"], properties: { source: enumString(["agent_agreed", "agent_adjudicated", "human_reviewed"]), sourcePairId: evalId, humanFeedbackId: { anyOf: [{ type: "null" }, evalId] }, corrected: { type: "boolean" } } } as const;
const goldPairSchema = { type: "object", additionalProperties: false, required: [...silverPairSchema.required, "goldLabel", "goldProvenance"], properties: { ...silverPairSchema.properties, goldLabel: enumString(["accept", "reject"]), goldProvenance: goldProvenanceSchema } } as const;

export const STAGE_A_GOLD_MANIFEST_V2_SCHEMA = {
  $id: "ai-filter-stage-a-gold-manifest-v2", type: "object", additionalProperties: false,
  required: ["schemaVersion", "status", "sourceSilverDigest", "auditPolicyDigest", "humanFeedbackDigest", "humanFeedbackId", ...coreRequired, "preAnnotationDigest", "promptReviewFeedbackDigest", "extractionManifest", "extractionManifestDigest", "reviewPlan", "pairs", "finalCritic"],
  properties: { schemaVersion: { const: "ai-filter-stage-a-gold-manifest-v2" }, status: { const: "human_audited_gold" }, sourceSilverDigest: digest, auditPolicyDigest: digest, humanFeedbackDigest: digest, humanFeedbackId: evalId, ...coreArtifactProperties, preAnnotationDigest: digest, promptReviewFeedbackDigest: digest, extractionManifest: extractionManifestSchema, extractionManifestDigest: digest, reviewPlan: reviewPlanSchema, pairs: { type: "array", minItems: 200, maxItems: 200, items: goldPairSchema }, finalCritic: finalCriticSchema },
} as const;

export const STAGE_A_GOLD_FREEZE_V2_SCHEMA = { $id: "ai-filter-stage-a-gold-freeze-v2", type: "object", additionalProperties: false, required: ["schemaVersion", "manifest", "goldDigest"], properties: { schemaVersion: { const: "ai-filter-stage-a-gold-freeze-v2" }, manifest: { $ref: "ai-filter-stage-a-gold-manifest-v2" }, goldDigest: digest } } as const;
