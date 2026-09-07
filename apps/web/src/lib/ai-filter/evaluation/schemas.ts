const idPattern = "^eval-[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$";
const digestPattern = "^[a-f0-9]{64}$";
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
    candidateId: { type: "string", minLength: 1, maxLength: 256 },
    title: { type: "string", minLength: 1, maxLength: 1_000 },
    companyName: { type: "string", minLength: 1, maxLength: 1_000 },
    descriptionHtml: { type: "string", minLength: 1, maxLength: 2_000_000 },
    selectedDescriptionLocale: { type: "string", enum: ["de", "en", "fr", "it"] },
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
      maxLength: 2_000,
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
    "examples",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-wip-v1" },
    datasetId: { type: "string", pattern: idPattern },
    classifierInputSchemaVersion: { const: "classifier-input-v1" },
    classifierInputNormalizerVersion: { const: "classifier-input-normalizer-v3" },
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
    schemaVersion: { const: "classifier-input-v1" },
    candidateId: { type: "string", minLength: 1, maxLength: 1_000 },
    title: { type: "string", minLength: 1, maxLength: 1_000 },
    companyName: { type: "string", minLength: 1, maxLength: 1_000 },
    descriptionText: { type: "string", minLength: 1, maxLength: 12_000 },
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
    "readyPolicy",
    "readyPolicyDigest",
    "examples",
  ],
  properties: {
    schemaVersion: { const: "ai-filter-stage-a-manifest-v1" },
    datasetId: { type: "string", pattern: idPattern },
    classifierInputSchemaVersion: { const: "classifier-input-v1" },
    classifierInputNormalizerVersion: { const: "classifier-input-normalizer-v3" },
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
