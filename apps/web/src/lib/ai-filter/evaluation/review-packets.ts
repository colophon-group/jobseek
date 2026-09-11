import { createHash } from "node:crypto";
import {
  normalizeClassifierInputV1,
  type ClassifierInputV1,
  type ClassifierInputSource,
} from "../classifier-input";
import { normalizeAiFilterSoftQueryV1 } from "../contract";
import {
  STAGE_A_MAX_HUMAN_AUDIT_PAIRS,
  StageAEvaluationError,
  canonicalStageAJson,
  digestStageAHumanAuditPolicy,
  validateStageAHumanAuditPolicy,
  validateStageASilverFreeze,
  validateStageAWip,
  type StageAGeneralizedFilterContextV2,
} from "./stage-a";

export const STAGE_A_PROMPT_REVIEW_CARD_COUNT = 12;
export const STAGE_A_CALIBRATION_MIN_EXAMPLES = 24;
export const STAGE_A_CALIBRATION_MAX_EXAMPLES = 32;

const EVAL_ID = /^eval-[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u;
const DIGEST = /^[a-f0-9]{64}$/u;
const BIDI_OR_INVISIBLE = /[\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/gu;
const UNSAFE_CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu;

type CalibrationExample = Readonly<{
  calibrationExampleId: string;
  softQuery: string;
  classifierSource: ClassifierInputSource;
  contentIdentity: string;
}>;

export type StageACalibrationArtifact = Readonly<{
  schemaVersion: "ai-filter-stage-a-calibration-v2";
  calibrationId: string;
  examples: readonly CalibrationExample[];
}>;

function fail(path: string, rule: string): never {
  throw new StageAEvaluationError(path, rule);
}

function plainRecord(input: unknown, path: string, fields: readonly string[]): Record<string, unknown> {
  try {
    if (typeof input !== "object" || input === null || Array.isArray(input)) fail(path, "object_required");
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail(path, "object_unreadable");
  }
  let descriptors: ReturnType<typeof Object.getOwnPropertyDescriptors>;
  try {
    descriptors = Object.getOwnPropertyDescriptors(input);
  } catch {
    fail(path, "object_unreadable");
  }
  const allowed = new Set(fields);
  const result = Object.create(null) as Record<string, unknown>;
  for (const key of Reflect.ownKeys(descriptors)) {
    if (typeof key !== "string" || !allowed.has(key)) fail(path, "additional_properties");
    const descriptor = descriptors[key];
    if (!descriptor.enumerable || !("value" in descriptor)) fail(path, "data_properties_required");
    result[key] = descriptor.value;
  }
  return result;
}

function plainArray(input: unknown, path: string, minimum: number, maximum: number): unknown[] {
  try {
    if (!Array.isArray(input)) fail(path, "array_required");
  } catch (error) {
    if (error instanceof StageAEvaluationError) throw error;
    fail(path, "array_unreadable");
  }
  let descriptors: ReturnType<typeof Object.getOwnPropertyDescriptors>;
  try {
    descriptors = Object.getOwnPropertyDescriptors(input);
  } catch {
    fail(path, "array_unreadable");
  }
  const length = descriptors.length?.value;
  if (!Number.isSafeInteger(length) || length < minimum || length > maximum) fail(path, "bounded_array_required");
  const result: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    const descriptor = descriptors[String(index)];
    if (!descriptor?.enumerable || !("value" in descriptor)) fail(path, "dense_array_required");
    result[index] = descriptor.value;
  }
  if (Reflect.ownKeys(descriptors).some((key) => key !== "length" && (typeof key !== "string" || !/^(?:0|[1-9]\d*)$/u.test(key) || Number(key) >= length))) fail(path, "additional_properties");
  return result;
}

function required(record: Record<string, unknown>, key: string, path: string): unknown {
  if (!Object.hasOwn(record, key)) fail(path, "required");
  return record[key];
}

function safeId(value: unknown, path: string): string {
  if (typeof value !== "string" || !EVAL_ID.test(value)) fail(path, "eval_id_required");
  return value;
}

function normalizedDisplayText(value: string): string {
  return value
    .replace(/\r\n?/gu, "\n")
    .replace(BIDI_OR_INVISIBLE, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`)
    .replace(UNSAFE_CONTROL, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function fenced(value: string): string {
  const text = normalizedDisplayText(value);
  const longest = Math.max(0, ...Array.from(text.matchAll(/`+/gu), ([run]) => run.length));
  const delimiter = "`".repeat(Math.max(3, longest + 1));
  return `${delimiter}text\n${text}\n${delimiter}`;
}

function contextLines(context: StageAGeneralizedFilterContextV2): string {
  return [
    ["Company", context.companyScope],
    ["Location", context.locationScope],
    ["Occupation", context.occupationScope],
    ["Keywords", context.keywordScope],
    ["Seniority", context.seniorityScope],
    ["Technology", context.technologyScope],
    ["Work mode", context.workModeScope],
    ["Employment type", context.employmentTypeScope],
    ["Compensation", context.compensationScope],
    ["Experience", context.experienceScope],
    ["Locale", context.locale],
  ].map(([label, value]) => `- ${label}: ${value}`).join("\n");
}

function requireUniqueKnownIds(
  input: readonly string[],
  expectedLength: number | null,
  known: ReadonlySet<string>,
  path: string,
): readonly string[] {
  const values = expectedLength === null
    ? plainArray(input, path, 1, STAGE_A_MAX_HUMAN_AUDIT_PAIRS)
    : plainArray(input, path, 0, STAGE_A_PROMPT_REVIEW_CARD_COUNT + 1);
  if (expectedLength !== null && values.length !== expectedLength) fail(path, "exact_card_count_required");
  const ids = values.map((value, index) => safeId(value, `${path}[${index}]`)).sort();
  if (new Set(ids).size !== ids.length) fail(path, "unique_ids_required");
  if (ids.some((id) => !known.has(id))) fail(path, "known_ids_required");
  return ids;
}

export function renderStageAPromptReviewPacket(
  wipInput: unknown,
  bundleIdsInput: readonly string[],
): string {
  const wip = validateStageAWip(wipInput);
  const bundleById = new Map(wip.bundles.map((bundle) => [bundle.bundleId, bundle]));
  const filterById = new Map(wip.filters.map((filter) => [filter.filterId, filter]));
  const bundleIds = requireUniqueKnownIds(bundleIdsInput, STAGE_A_PROMPT_REVIEW_CARD_COUNT, new Set(bundleById.keys()), "$bundleIds");
  const cards = bundleIds.map((bundleId, index) => {
    const bundle = bundleById.get(bundleId)!;
    const filter = filterById.get(bundle.filterId)!;
    const titles = wip.pairs
      .filter((pair) => pair.bundleId === bundleId)
      .slice(0, 3)
      .map(({ classifierSource }) => normalizeClassifierInputV1(classifierSource).payload.title);
    return [
      `## ${index + 1}. ${bundleId}`,
      "",
      `Cohort: ${bundle.cohort}`,
      `Persona: ${bundle.persona}`,
      "",
      "Generalized filter context:",
      contextLines(filter.generalizedContext),
      "",
      "Prompt:",
      fenced(bundle.softQuery),
      "",
      "Three feed titles:",
      ...titles.flatMap((title, titleIndex) => [`${titleIndex + 1}.`, fenced(title)]),
      "",
      "Decision: [ ] keep  [ ] revise  [ ] reject",
    ].join("\n");
  });
  return `# Stage A prompt sanity review\n\n${cards.join("\n\n---\n\n")}\n`;
}

export function renderStageALabelAuditPacket(
  silverInput: unknown,
  expectedSilverDigest: string,
  expectedCalibrationDigest: string,
  policyInput: unknown,
  expectedPolicyDigest: string,
): string {
  const silver = validateStageASilverFreeze(silverInput, expectedSilverDigest, expectedCalibrationDigest);
  const policy = validateStageAHumanAuditPolicy(policyInput);
  if (
    policy.sourceSilverDigest !== silver.silverDigest ||
    digestStageAHumanAuditPolicy(policy) !== expectedPolicyDigest
  ) {
    fail("$policy", "audit_policy_pin_mismatch");
  }
  const pairById = new Map(silver.manifest.pairs.map((pair) => [pair.pairId, pair]));
  const bundleById = new Map(silver.manifest.bundles.map((bundle) => [bundle.bundleId, bundle]));
  const pairIds = requireUniqueKnownIds(policy.auditPairIds, null, new Set(pairById.keys()), "$policy.auditPairIds");
  const cards = pairIds.map((pairId, index) => {
    const pair = pairById.get(pairId)!;
    const bundle = bundleById.get(pair.bundleId)!;
    return [
      `## ${index + 1}. ${pairId}`,
      "",
      `Cohort: ${bundle.cohort}`,
      "",
      "Prompt:",
      fenced(bundle.softQuery),
      "",
      "Normalized posting:",
      "",
      "Title:",
      fenced(pair.classifierInput.title),
      "",
      "Company:",
      fenced(pair.classifierInput.companyName),
      "",
      "Description:",
      fenced(pair.classifierInput.descriptionText),
      "",
      "Judgment: [ ] accept  [ ] reject  [ ] unclear",
    ].join("\n");
  });
  return `# Stage A blind label audit\n\n${cards.join("\n\n---\n\n")}\n`;
}

function validateCalibrationArtifact(input: unknown): Readonly<{
  calibrationId: string;
  examples: readonly Readonly<{
    calibrationExampleId: string;
    softQuery: string;
    classifierInput: ClassifierInputV1;
  }>[];
}> {
  const record = plainRecord(input, "$calibration", ["schemaVersion", "calibrationId", "examples"]);
  if (required(record, "schemaVersion", "$calibration.schemaVersion") !== "ai-filter-stage-a-calibration-v2") fail("$calibration.schemaVersion", "literal_required");
  const calibrationId = safeId(required(record, "calibrationId", "$calibration.calibrationId"), "$calibration.calibrationId");
  const rawExamples = plainArray(required(record, "examples", "$calibration.examples"), "$calibration.examples", STAGE_A_CALIBRATION_MIN_EXAMPLES, STAGE_A_CALIBRATION_MAX_EXAMPLES);
  const examples = rawExamples.map((raw, index) => {
    const examplePath = `$calibration.examples[${index}]`;
    const example = plainRecord(raw, examplePath, ["calibrationExampleId", "softQuery", "classifierSource", "contentIdentity"]);
    const calibrationExampleId = safeId(required(example, "calibrationExampleId", `${examplePath}.calibrationExampleId`), `${examplePath}.calibrationExampleId`);
    let softQuery: string;
    let normalized;
    try {
      const rawQuery = required(example, "softQuery", `${examplePath}.softQuery`);
      softQuery = normalizeAiFilterSoftQueryV1(rawQuery);
      if (softQuery !== rawQuery) fail(`${examplePath}.softQuery`, "canonical_query_required");
      normalized = normalizeClassifierInputV1(required(example, "classifierSource", `${examplePath}.classifierSource`) as ClassifierInputSource);
    } catch (error) {
      if (error instanceof StageAEvaluationError) throw error;
      fail(examplePath, "calibration_input_invalid");
    }
    const identity = required(example, "contentIdentity", `${examplePath}.contentIdentity`);
    if (typeof identity !== "string" || !DIGEST.test(identity) || identity !== normalized.contentIdentity) fail(`${examplePath}.contentIdentity`, "content_identity_mismatch");
    return Object.freeze({ calibrationExampleId, softQuery, classifierInput: normalized.payload });
  }).sort((left, right) => left.calibrationExampleId < right.calibrationExampleId ? -1 : left.calibrationExampleId > right.calibrationExampleId ? 1 : 0);
  if (new Set(examples.map(({ calibrationExampleId }) => calibrationExampleId)).size !== examples.length) fail("$calibration.examples", "unique_ids_required");
  return Object.freeze({ calibrationId, examples: Object.freeze(examples) });
}

export function renderStageACalibrationPacket(input: unknown): string {
  const calibration = validateCalibrationArtifact(input);
  const cards = calibration.examples.map((example, index) => [
    `## ${index + 1}. ${example.calibrationExampleId}`,
    "",
    "Synthetic prompt:",
    fenced(example.softQuery),
    "",
    "Normalized posting:",
    "",
    "Title:",
    fenced(example.classifierInput.title),
    "",
    "Company:",
    fenced(example.classifierInput.companyName),
    "",
    "Description:",
    fenced(example.classifierInput.descriptionText),
    "",
    "Judgment: [ ] accept  [ ] reject  [ ] unclear",
  ].join("\n"));
  return `# Stage A calibration review: ${calibration.calibrationId}\n\n${cards.join("\n\n---\n\n")}\n`;
}

export function digestStageACalibrationArtifact(input: unknown): string {
  const calibration = validateCalibrationArtifact(input);
  return createHash("sha256")
    .update("ai-filter-stage-a-calibration-v2\n", "utf8")
    .update(canonicalStageAJson(calibration), "utf8")
    .digest("hex");
}
