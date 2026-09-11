import {
  CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT,
  CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT,
  CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT,
  CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
} from "../classifier-input";
import {
  AI_FILTER_QUERY_MAX_LENGTH,
  AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
} from "../contract";

const idPattern = "^eval-[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$";
const digestPattern = "^[a-f0-9]{64}$";
const candidateIdPattern =
  "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$";
const provenanceTokenPattern = "^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$";
const canonicalQueryPattern =
  "^(?![\\s\\S]*(?:\\p{Cc}|\\p{Default_Ignorable_Code_Point}))(?![\\s\\S]*[^\\S ])(?:\\S|\\S(?:[^\\s]| (?! ))*\\S)$";

const enumString = (values: readonly string[]) => ({ type: "string", enum: values });

const classifierSourceSchema = {
  type: "object",
  additionalProperties: false,
  required: [
    "candidateId",
    "title",
    "companyName",
    "descriptionHtml",
    "selectedDescriptionLocale",
  ],
  properties: {
    candidateId: { type: "string", pattern: candidateIdPattern },
    title: { type: "string", maxLength: CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT },
    companyName: {
      type: "string",
      maxLength: CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
    },
    descriptionHtml: {
      type: "string",
      maxLength: CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT,
    },
    selectedDescriptionLocale: {
      type: "string",
      maxLength: CLASSIFIER_INLINE_TEXT_RAW_CODE_UNIT_LIMIT,
    },
  },
} as const;

const classifierInputSchema = {
  type: "object",
  additionalProperties: false,
  required: ["schemaVersion", "candidateId", "title", "companyName", "descriptionText"],
  properties: {
    schemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
    candidateId: { type: "string", pattern: candidateIdPattern },
    title: {
      type: "string",
      minLength: 1,
      maxLength: CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT,
    },
    companyName: {
      type: "string",
      minLength: 1,
      maxLength: CLASSIFIER_INLINE_TEXT_CODE_POINT_LIMIT,
    },
    descriptionText: {
      type: "string",
      minLength: 1,
      maxLength: CLASSIFIER_DESCRIPTION_CODE_POINT_LIMIT,
    },
  },
} as const;

const generalizedContextSchema = {
  type: "object",
  additionalProperties: false,
  required: [
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
  ],
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

const provenanceSchema = {
  type: "object",
  additionalProperties: false,
  required: [
    "origin",
    "authorId",
    "agentRole",
    "model",
    "modelVersion",
    "reasoningEffort",
    "taskPromptDigest",
  ],
  properties: {
    origin: { const: "agent_synthetic" },
    authorId: { type: "string", pattern: idPattern },
    agentRole: { type: "string", pattern: provenanceTokenPattern },
    model: { type: "string", pattern: provenanceTokenPattern },
    modelVersion: { type: "string", pattern: provenanceTokenPattern },
    reasoningEffort: enumString(["low", "medium", "high", "xhigh", "max", "ultra"]),
    taskPromptDigest: { type: "string", pattern: digestPattern },
  },
} as const;

const annotationSchema = {
  type: "object",
  additionalProperties: false,
  required: ["annotationId", "actorId", "label"],
  properties: {
    annotationId: { type: "string", pattern: idPattern },
    actorId: { type: "string", pattern: idPattern },
    label: enumString(["accept", "reject"]),
  },
} as const;

const adjudicationSchema = {
  type: "object",
  additionalProperties: false,
  required: ["adjudicationId", "actorId", "label"],
  properties: {
    adjudicationId: { type: "string", pattern: idPattern },
    actorId: { type: "string", pattern: idPattern },
    label: enumString(["accept", "reject"]),
  },
} as const;

export const STAGE_A_FILTER_V2_SCHEMA = {
  $id: "ai-filter-stage-a-filter-v2",
  type: "object",
  additionalProperties: false,
  required: [
    "schemaVersion",
    "filterId",
    "source",
    "sourceFilterDigest",
    "generalizedContext",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-filter-v2" },
    filterId: { type: "string", pattern: idPattern },
    source: { const: "production_deidentified" },
    sourceFilterDigest: { type: "string", pattern: digestPattern },
    generalizedContext: generalizedContextSchema,
  },
} as const;

export const STAGE_A_BUNDLE_V2_SCHEMA = {
  $id: "ai-filter-stage-a-bundle-v2",
  type: "object",
  additionalProperties: false,
  required: [
    "schemaVersion",
    "bundleId",
    "filterId",
    "cohort",
    "persona",
    "softQuery",
    "promptProvenance",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-bundle-v2" },
    bundleId: { type: "string", pattern: idPattern },
    filterId: { type: "string", pattern: idPattern },
    cohort: enumString(["production_shaped", "challenge"]),
    persona: enumString([
      "lazy",
      "verbose",
      "misunderstood_purpose",
      "precise",
      "vague",
      "contradictory",
      "multilingual",
    ]),
    softQuery: {
      type: "string",
      minLength: 1,
      maxLength: AI_FILTER_QUERY_MAX_LENGTH,
      pattern: canonicalQueryPattern,
    },
    promptProvenance: provenanceSchema,
  },
} as const;

export const STAGE_A_PAIR_V2_SCHEMA = {
  $id: "ai-filter-stage-a-pair-v2",
  type: "object",
  additionalProperties: false,
  required: [
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
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-pair-v2" },
    pairId: { type: "string", pattern: idPattern },
    bundleId: { type: "string", pattern: idPattern },
    locale: enumString(["de", "en", "fr", "it"]),
    evidenceCondition: enumString([
      "direct_support",
      "direct_conflict",
      "insufficient_evidence",
      "policy_boundary",
      "prompt_injection",
    ]),
    ambiguity: { type: "boolean" },
    classifierSource: classifierSourceSchema,
    contentIdentity: { type: "string", pattern: digestPattern },
    annotations: {
      type: "array",
      minItems: 2,
      maxItems: 2,
      items: annotationSchema,
    },
    adjudication: { anyOf: [{ type: "null" }, adjudicationSchema] },
  },
} as const;

const finalCriticSchema = {
  type: "object",
  additionalProperties: false,
  required: ["reviewId", "actorId", "approved"],
  properties: {
    reviewId: { type: "string", pattern: idPattern },
    actorId: { type: "string", pattern: idPattern },
    approved: { const: true },
  },
} as const;

export const STAGE_A_WIP_V2_SCHEMA = {
  $id: "ai-filter-stage-a-wip-v2",
  type: "object",
  additionalProperties: false,
  required: [
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
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-wip-v2" },
    datasetId: { type: "string", pattern: idPattern },
    classifierInputSchemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
    classifierInputNormalizerVersion: { const: CLASSIFIER_INPUT_NORMALIZER_VERSION },
    softQueryNormalizerVersion: { const: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION },
    calibrationDigest: { type: "string", pattern: digestPattern },
    filters: {
      type: "array",
      minItems: 20,
      maxItems: 20,
      items: { $ref: "ai-filter-stage-a-filter-v2" },
    },
    bundles: {
      type: "array",
      minItems: 25,
      maxItems: 25,
      items: { $ref: "ai-filter-stage-a-bundle-v2" },
    },
    pairs: {
      type: "array",
      minItems: 200,
      maxItems: 200,
      items: { $ref: "ai-filter-stage-a-pair-v2" },
    },
    finalCritic: finalCriticSchema,
  },
} as const;

const silverProvenanceSchema = {
  type: "object",
  additionalProperties: false,
  required: ["method", "annotationIds", "adjudicationId"],
  properties: {
    method: enumString(["agreement", "adjudication"]),
    annotationIds: {
      type: "array",
      minItems: 2,
      maxItems: 2,
      items: { type: "string", pattern: idPattern },
    },
    adjudicationId: {
      anyOf: [{ type: "null" }, { type: "string", pattern: idPattern }],
    },
  },
} as const;

const silverPairSchema = {
  type: "object",
  additionalProperties: false,
  required: [
    ...STAGE_A_PAIR_V2_SCHEMA.required,
    "classifierInput",
    "silverLabel",
    "silverProvenance",
  ],
  properties: {
    ...STAGE_A_PAIR_V2_SCHEMA.properties,
    classifierInput: classifierInputSchema,
    silverLabel: enumString(["accept", "reject"]),
    silverProvenance: silverProvenanceSchema,
  },
} as const;

export const STAGE_A_SILVER_MANIFEST_V2_SCHEMA = {
  $id: "ai-filter-stage-a-silver-manifest-v2",
  type: "object",
  additionalProperties: false,
  required: [
    "schemaVersion",
    "status",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "softQueryNormalizerVersion",
    "calibrationDigest",
    "sourceWipDigest",
    "filters",
    "bundles",
    "pairs",
    "finalCritic",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-silver-manifest-v2" },
    status: { const: "agent_adjudicated_silver" },
    datasetId: { type: "string", pattern: idPattern },
    classifierInputSchemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
    classifierInputNormalizerVersion: { const: CLASSIFIER_INPUT_NORMALIZER_VERSION },
    softQueryNormalizerVersion: { const: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION },
    calibrationDigest: { type: "string", pattern: digestPattern },
    sourceWipDigest: { type: "string", pattern: digestPattern },
    filters: {
      type: "array",
      minItems: 20,
      maxItems: 20,
      items: { $ref: "ai-filter-stage-a-filter-v2" },
    },
    bundles: {
      type: "array",
      minItems: 25,
      maxItems: 25,
      items: { $ref: "ai-filter-stage-a-bundle-v2" },
    },
    pairs: {
      type: "array",
      minItems: 200,
      maxItems: 200,
      items: silverPairSchema,
    },
    finalCritic: finalCriticSchema,
  },
} as const;

export const STAGE_A_SILVER_FREEZE_V2_SCHEMA = {
  $id: "ai-filter-stage-a-silver-freeze-v2",
  type: "object",
  additionalProperties: false,
  required: ["schemaVersion", "manifest", "silverDigest"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-silver-freeze-v2" },
    manifest: { $ref: "ai-filter-stage-a-silver-manifest-v2" },
    silverDigest: { type: "string", pattern: digestPattern },
  },
} as const;

export const STAGE_A_HUMAN_AUDIT_POLICY_V2_SCHEMA = {
  $id: "ai-filter-stage-a-human-audit-policy-v2",
  type: "object",
  additionalProperties: false,
  required: ["schemaVersion", "sourceSilverDigest", "auditPairIds"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-human-audit-policy-v2" },
    sourceSilverDigest: { type: "string", pattern: digestPattern },
    auditPairIds: {
      type: "array",
      minItems: 1,
      maxItems: 32,
      uniqueItems: true,
      items: { type: "string", pattern: idPattern },
    },
  },
} as const;

export const STAGE_A_HUMAN_FEEDBACK_V2_SCHEMA = {
  $id: "ai-filter-stage-a-human-feedback-v2",
  type: "object",
  additionalProperties: false,
  required: [
    "schemaVersion",
    "feedbackId",
    "reviewerId",
    "sourceSilverDigest",
    "auditPolicyDigest",
    "approved",
    "decisions",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-human-feedback-v2" },
    feedbackId: { type: "string", pattern: idPattern },
    reviewerId: { type: "string", pattern: idPattern },
    sourceSilverDigest: { type: "string", pattern: digestPattern },
    auditPolicyDigest: { type: "string", pattern: digestPattern },
    approved: { type: "boolean" },
    decisions: {
      type: "array",
      minItems: 1,
      maxItems: 32,
      items: {
        type: "object",
        additionalProperties: false,
        required: ["pairId", "judgment"],
        properties: {
          pairId: { type: "string", pattern: idPattern },
          judgment: enumString(["accept", "reject", "unclear"]),
        },
      },
    },
  },
} as const;

const goldProvenanceSchema = {
  type: "object",
  additionalProperties: false,
  required: ["source", "sourcePairId", "humanFeedbackId"],
  properties: {
    source: enumString(["silver", "human_correction"]),
    sourcePairId: { type: "string", pattern: idPattern },
    humanFeedbackId: {
      anyOf: [{ type: "null" }, { type: "string", pattern: idPattern }],
    },
  },
} as const;

const goldPairSchema = {
  type: "object",
  additionalProperties: false,
  required: [
    ...silverPairSchema.required,
    "goldLabel",
    "goldProvenance",
  ],
  properties: {
    ...silverPairSchema.properties,
    goldLabel: enumString(["accept", "reject"]),
    goldProvenance: goldProvenanceSchema,
  },
} as const;

export const STAGE_A_GOLD_MANIFEST_V2_SCHEMA = {
  $id: "ai-filter-stage-a-gold-manifest-v2",
  type: "object",
  additionalProperties: false,
  required: [
    "schemaVersion",
    "status",
    "sourceSilverDigest",
    "auditPolicyDigest",
    "humanFeedbackDigest",
    "humanFeedbackId",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "softQueryNormalizerVersion",
    "calibrationDigest",
    "filters",
    "bundles",
    "pairs",
    "finalCritic",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-gold-manifest-v2" },
    status: { const: "human_audited_gold" },
    sourceSilverDigest: { type: "string", pattern: digestPattern },
    auditPolicyDigest: { type: "string", pattern: digestPattern },
    humanFeedbackDigest: { type: "string", pattern: digestPattern },
    humanFeedbackId: { type: "string", pattern: idPattern },
    datasetId: { type: "string", pattern: idPattern },
    classifierInputSchemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
    classifierInputNormalizerVersion: { const: CLASSIFIER_INPUT_NORMALIZER_VERSION },
    softQueryNormalizerVersion: { const: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION },
    calibrationDigest: { type: "string", pattern: digestPattern },
    filters: {
      type: "array",
      minItems: 20,
      maxItems: 20,
      items: { $ref: "ai-filter-stage-a-filter-v2" },
    },
    bundles: {
      type: "array",
      minItems: 25,
      maxItems: 25,
      items: { $ref: "ai-filter-stage-a-bundle-v2" },
    },
    pairs: {
      type: "array",
      minItems: 200,
      maxItems: 200,
      items: goldPairSchema,
    },
    finalCritic: finalCriticSchema,
  },
} as const;

export const STAGE_A_GOLD_FREEZE_V2_SCHEMA = {
  $id: "ai-filter-stage-a-gold-freeze-v2",
  type: "object",
  additionalProperties: false,
  required: ["schemaVersion", "manifest", "goldDigest"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-gold-freeze-v2" },
    manifest: { $ref: "ai-filter-stage-a-gold-manifest-v2" },
    goldDigest: { type: "string", pattern: digestPattern },
  },
} as const;
