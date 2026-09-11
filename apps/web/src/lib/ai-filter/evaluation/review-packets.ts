import { normalizeClassifierInputV1 } from "../classifier-input";
import {
  deriveStageAHumanAuditPolicy,
  deriveStageAPromptReviewBundleIds,
  digestStageACalibrationArtifact,
  validateStageACalibrationArtifact,
  validateStageAPreAnnotation,
  validateStageASilverFreeze,
  type StageACalibrationArtifactV2,
  type StageAGeneralizedFilterContextV2,
} from "./stage-a";

export const STAGE_A_PROMPT_REVIEW_CARD_COUNT = 12;
const BIDI_OR_INVISIBLE = /[\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/gu;
const UNSAFE_CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu;

export type StageACalibrationArtifact = StageACalibrationArtifactV2;
export { digestStageACalibrationArtifact };

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

export function renderStageAPromptReviewPacket(
  preAnnotationInput: unknown,
): string {
  const pre = validateStageAPreAnnotation(preAnnotationInput);
  const bundleById = new Map(pre.bundles.map((bundle) => [bundle.bundleId, bundle]));
  const filterById = new Map(pre.filters.map((filter) => [filter.filterId, filter]));
  const bundleIds = deriveStageAPromptReviewBundleIds(pre);
  const cards = bundleIds.map((bundleId, index) => {
    const bundle = bundleById.get(bundleId)!;
    const filter = filterById.get(bundle.filterId)!;
    const titles = pre.pairs
      .filter((pair) => pair.bundleId === bundleId)
      .slice(0, 3)
      .map(({ classifierSource }) => normalizeClassifierInputV1(classifierSource).payload.title);
    return [
      `## ${index + 1}. ${bundleId}`,
      "",
      `Cohort: ${bundle.cohort}`,
      `Persona: ${bundle.persona}`,
      `Prompt locale: ${bundle.promptLocale}`,
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
): string {
  const silver = validateStageASilverFreeze(silverInput, expectedSilverDigest);
  const policy = deriveStageAHumanAuditPolicy(silver, expectedSilverDigest);
  const pairById = new Map(silver.manifest.pairs.map((pair) => [pair.pairId, pair]));
  const queryByBundle = new Map(silver.manifest.bundles.map(({ bundleId, softQuery }) => [bundleId, softQuery]));
  const cards = policy.auditPairIds.map((pairId, index) => {
    const pair = pairById.get(pairId)!;
    return [
      `## ${index + 1}. ${pairId}`,
      "",
      "Prompt:",
      fenced(queryByBundle.get(pair.bundleId)!),
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

export function renderStageACalibrationPacket(input: unknown): string {
  const calibration = validateStageACalibrationArtifact(input);
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
