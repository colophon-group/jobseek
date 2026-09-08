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
const canonicalQueryPattern =
  "^(?![\\s\\S]*(?:\\p{Cc}|\\p{Default_Ignorable_Code_Point}))(?![\\s\\S]*[^\\S ])(?:\\S|\\S(?:[^\\s]| (?! ))*\\S)$";

const annotationSchema = {
  type: "object",
  additionalProperties: false,
  required: ["annotationId", "actorId", "label"],
  properties: {
    annotationId: { type: "string", pattern: idPattern },
    actorId: { type: "string", pattern: idPattern },
    label: { type: "integer", enum: [0, 1] },
  },
} as const;

const adjudicationSchema = {
  type: "object",
  additionalProperties: false,
  required: ["adjudicationId", "actorId", "label"],
  properties: {
    adjudicationId: { type: "string", pattern: idPattern },
    actorId: { type: "string", pattern: idPattern },
    label: { type: "integer", enum: [0, 1] },
  },
} as const;

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

export const STAGE_A_EXAMPLE_V1_SCHEMA = {
  $id: "ai-filter-stage-a-example-v1",
  type: "object",
  additionalProperties: false,
  required: [
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
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-example-v1" },
    exampleId: { type: "string", pattern: idPattern },
    softQuery: {
      type: "string",
      minLength: 1,
      maxLength: AI_FILTER_QUERY_MAX_LENGTH,
      pattern: canonicalQueryPattern,
    },
    queryOrigin: { const: "eval_authored" },
    locale: { type: "string", enum: ["de", "en", "fr", "it"] },
    scenario: {
      type: "string",
      enum: ["clear_match", "clear_non_match", "ambiguous", "prompt_injection"],
    },
    classifierSource: classifierSourceSchema,
    contentIdentity: { type: "string", pattern: digestPattern },
    annotations: {
      type: "array",
      minItems: 1,
      maxItems: 2,
      items: annotationSchema,
    },
    adjudication: { anyOf: [{ type: "null" }, adjudicationSchema] },
  },
} as const;

export const STAGE_A_WIP_V1_SCHEMA = {
  $id: "ai-filter-stage-a-wip-v1",
  type: "object",
  additionalProperties: false,
  required: [
    "schemaVersion",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "softQueryNormalizerVersion",
    "examples",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-wip-v1" },
    datasetId: { type: "string", pattern: idPattern },
    classifierInputSchemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
    classifierInputNormalizerVersion: { const: CLASSIFIER_INPUT_NORMALIZER_VERSION },
    softQueryNormalizerVersion: { const: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION },
    examples: {
      type: "array",
      maxItems: 200,
      items: { $ref: "ai-filter-stage-a-example-v1" },
    },
  },
} as const;

export const STAGE_A_READY_POLICY_V1_SCHEMA = {
  $id: "ai-filter-stage-a-ready-policy-v1",
  type: "object",
  additionalProperties: false,
  required: ["schemaVersion", "localeMinimums", "scenarioMinimums"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-ready-policy-v1" },
    localeMinimums: {
      type: "object",
      additionalProperties: false,
      required: ["de", "en", "fr", "it"],
      properties: Object.fromEntries(
        ["de", "en", "fr", "it"].map((key) => [
          key,
          { type: "integer", minimum: 1, maximum: 200 },
        ]),
      ),
    },
    scenarioMinimums: {
      type: "object",
      additionalProperties: false,
      required: ["clear_match", "clear_non_match", "ambiguous", "prompt_injection"],
      properties: Object.fromEntries(
        ["clear_match", "clear_non_match", "ambiguous", "prompt_injection"].map((key) => [
          key,
          { type: "integer", minimum: 1, maximum: 200 },
        ]),
      ),
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

const readyExampleSchema = {
  type: "object",
  additionalProperties: false,
  required: [...STAGE_A_EXAMPLE_V1_SCHEMA.required, "classifierInput", "goldLabel"],
  properties: {
    ...STAGE_A_EXAMPLE_V1_SCHEMA.properties,
    classifierInput: classifierInputSchema,
    goldLabel: { type: "integer", enum: [0, 1] },
  },
} as const;

export const STAGE_A_MANIFEST_V1_SCHEMA = {
  $id: "ai-filter-stage-a-manifest-v1",
  type: "object",
  additionalProperties: false,
  required: [
    "schemaVersion",
    "datasetId",
    "classifierInputSchemaVersion",
    "classifierInputNormalizerVersion",
    "softQueryNormalizerVersion",
    "readyPolicy",
    "readyPolicyDigest",
    "examples",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-manifest-v1" },
    datasetId: { type: "string", pattern: idPattern },
    classifierInputSchemaVersion: { const: CLASSIFIER_INPUT_SCHEMA_VERSION },
    classifierInputNormalizerVersion: { const: CLASSIFIER_INPUT_NORMALIZER_VERSION },
    softQueryNormalizerVersion: { const: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION },
    readyPolicy: { $ref: "ai-filter-stage-a-ready-policy-v1" },
    readyPolicyDigest: { type: "string", pattern: digestPattern },
    examples: {
      type: "array",
      minItems: 200,
      maxItems: 200,
      items: readyExampleSchema,
    },
  },
} as const;

export const STAGE_A_FREEZE_V1_SCHEMA = {
  $id: "ai-filter-stage-a-freeze-v1",
  type: "object",
  additionalProperties: false,
  required: ["schemaVersion", "manifest", "manifestDigest"],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-freeze-v1" },
    manifest: { $ref: "ai-filter-stage-a-manifest-v1" },
    manifestDigest: { type: "string", pattern: digestPattern },
  },
} as const;
