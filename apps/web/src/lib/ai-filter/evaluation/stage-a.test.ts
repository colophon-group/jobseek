import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { restoreTestEnv, setTestEnv, snapshotTestEnv } from "@/test-utils/env";
import Ajv from "ajv";
import { afterEach, describe, expect, it } from "vitest";
import { CLASSIFIER_INPUT_NORMALIZER_VERSION, CLASSIFIER_INPUT_SCHEMA_VERSION, normalizeClassifierInputV1 } from "../classifier-input";
import { AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION } from "../contract";
import {
  STAGE_A_BUNDLE_V2_SCHEMA,
  STAGE_A_CALIBRATION_ARTIFACT_V2_SCHEMA,
  STAGE_A_CALIBRATION_RESULT_V2_SCHEMA,
  STAGE_A_FILTER_V2_SCHEMA,
  STAGE_A_GOLD_FREEZE_V2_SCHEMA,
  STAGE_A_GOLD_MANIFEST_V2_SCHEMA,
  STAGE_A_HUMAN_AUDIT_POLICY_V2_SCHEMA,
  STAGE_A_HUMAN_FEEDBACK_V2_SCHEMA,
  STAGE_A_PAIR_V2_SCHEMA,
  STAGE_A_PRE_ANNOTATION_PAIR_V2_SCHEMA,
  STAGE_A_PRE_ANNOTATION_V2_SCHEMA,
  STAGE_A_PROMPT_REVIEW_FEEDBACK_V2_SCHEMA,
  STAGE_A_SILVER_FREEZE_V2_SCHEMA,
  STAGE_A_SILVER_MANIFEST_V2_SCHEMA,
  STAGE_A_WIP_V2_SCHEMA,
} from "./schemas";
import {
  STAGE_A_BUNDLE_SCHEMA_VERSION,
  STAGE_A_CALIBRATION_RESULT_SCHEMA_VERSION,
  STAGE_A_EXTRACTION_MANIFEST_SCHEMA_VERSION,
  STAGE_A_FILTER_SCHEMA_VERSION,
  STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION,
  STAGE_A_PAIR_SCHEMA_VERSION,
  STAGE_A_PRE_ANNOTATION_SCHEMA_VERSION,
  STAGE_A_PROMPT_REVIEW_FEEDBACK_SCHEMA_VERSION,
  STAGE_A_WIP_SCHEMA_VERSION,
  StageAEvaluationError,
  canonicalStageAJson,
  deriveStageAHumanAuditPolicy,
  deriveStageAPromptReviewBundleIds,
  digestStageACalibrationArtifact,
  digestStageACalibrationResult,
  digestStageAExtractionManifest,
  digestStageAHumanAuditPolicy,
  digestStageAPreAnnotation,
  digestStageAPromptReviewFeedback,
  digestStageAResolvedCalibrationGroundTruth,
  digestStageASourceSnapshotIdentity,
  digestStageAWipReviewPayload,
  freezeStageASilver,
  loadStageAScoringLabels,
  loadStageATargetInputs,
  promoteStageAGold,
  reportStageAGoldFreeze,
  validateStageAWip,
  writeStageASilverFreezeFile,
  type StageACalibrationResultV2,
  type StageACalibrationArtifactV2,
  type StageAHumanFeedbackV2,
  type StageAPreAnnotationV2,
  type StageAPromptReviewFeedbackV2,
  type StageAWipV2,
} from "./stage-a";
import { renderStageALabelAuditPacket, renderStageAPromptReviewPacket } from "./review-packets";

const temporaryRoots: string[] = [];
const originalEnv = snapshotTestEnv(["AI_FILTER_EVAL_DATA_ROOT"]);
const D = (character: string) => character.repeat(64);

afterEach(async () => {
  restoreTestEnv(originalEnv);
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

type Mutable<T> = T extends readonly (infer Item)[] ? Mutable<Item>[] : T extends object ? { -readonly [Key in keyof T]: Mutable<T[Key]> } : T;
function clone<T>(input: T): Mutable<T> { return JSON.parse(JSON.stringify(input)) as Mutable<T>; }
function reapprove(wip: Mutable<StageAWipV2>): void {
  const { finalCritic, ...reviewPayload } = wip;
  finalCritic.reviewedWipDigest = digestStageAWipReviewPayload(reviewPayload);
}

function source(index: number) {
  return {
    candidateId: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    title: `Synthetic role ${index}`,
    companyName: `Synthetic Company ${index % 13}`,
    descriptionHtml: `<p>Synthetic evidence ${index}</p>`,
    selectedDescriptionLocale: ["de", "en", "fr", "it"][index % 4],
  };
}

function fixture() {
  const calibrationArtifact: StageACalibrationArtifactV2 = {
    schemaVersion: "ai-filter-stage-a-calibration-v2",
    calibrationId: "eval-calibration",
    examples: Array.from({ length: 25 }, (_, index) => {
      const classifierSource = {
        ...source(index + 500),
        candidateId: `10000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
      };
      return {
        calibrationExampleId: `eval-calibration-example-${String(index).padStart(2, "0")}`,
        softQuery: `calibration prompt ${String(index).padStart(2, "0")}`,
        classifierSource,
        contentIdentity: normalizeClassifierInputV1(classifierSource).contentIdentity,
      };
    }),
  };
  const calibrationInputDigest = digestStageACalibrationArtifact(calibrationArtifact);
  const calibrationDecisions: StageACalibrationResultV2["humanReview"]["decisions"] = Array.from({ length: 25 }, (_, index) => ({
    calibrationExampleId: `eval-calibration-example-${String(index).padStart(2, "0")}`,
    judgment: index === 24 ? "unclear" as const : index % 2 ? "reject" as const : "accept" as const,
  }));
  const candidateConfigs: StageACalibrationResultV2["candidateConfigs"] = [
    { configId: "eval-config-prompt-a", role: "prompt_author", model: "gpt-5.6-terra", modelVersion: "2026-09", reasoningEffort: "medium", taskPromptDigest: D("1") },
    { configId: "eval-config-prompt-b", role: "prompt_author", model: "gpt-5.6-sol", modelVersion: "2026-09", reasoningEffort: "high", taskPromptDigest: D("2") },
    { configId: "eval-config-annotator-a", role: "annotator", model: "gpt-5.6-terra", modelVersion: "2026-09", reasoningEffort: "high", taskPromptDigest: D("3") },
    { configId: "eval-config-annotator-b", role: "annotator", model: "gpt-5.6-sol", modelVersion: "2026-09", reasoningEffort: "high", taskPromptDigest: D("4") },
    { configId: "eval-config-adjudicator-a", role: "adjudicator", model: "gpt-5.6-sol", modelVersion: "2026-09", reasoningEffort: "high", taskPromptDigest: D("5") },
    { configId: "eval-config-adjudicator-b", role: "adjudicator", model: "gpt-5.6-terra", modelVersion: "2026-09", reasoningEffort: "xhigh", taskPromptDigest: D("6") },
    { configId: "eval-config-critic-a", role: "final_critic", model: "gpt-5.6-sol", modelVersion: "2026-09", reasoningEffort: "xhigh", taskPromptDigest: D("7") },
    { configId: "eval-config-critic-b", role: "final_critic", model: "gpt-5.6-terra", modelVersion: "2026-09", reasoningEffort: "max", taskPromptDigest: D("8") },
  ];
  const resolvedGroundTruthDigest = digestStageAResolvedCalibrationGroundTruth(calibrationDecisions);
  const suiteByRole = {
    prompt_author: { suiteKind: "same_eight_disposable_feeds" as const, suiteInputDigest: D("9"), sampleCount: 8 },
    annotator: { suiteKind: "resolved_human_ground_truth" as const, suiteInputDigest: resolvedGroundTruthDigest, sampleCount: 24 },
    adjudicator: { suiteKind: "seeded_conflicts" as const, suiteInputDigest: D("a"), sampleCount: 8 },
    final_critic: { suiteKind: "seeded_defects" as const, suiteInputDigest: D("b"), sampleCount: 8 },
  };
  const calibrationResult: StageACalibrationResultV2 = {
    schemaVersion: STAGE_A_CALIBRATION_RESULT_SCHEMA_VERSION,
    calibrationId: "eval-calibration",
    calibrationInputDigest,
    humanReview: {
      reviewId: "eval-calibration-review",
      reviewerId: "eval-calibration-human",
      approved: true,
      decisions: calibrationDecisions,
    },
    candidateConfigs,
    trials: candidateConfigs.map((config, index) => ({
      trialId: `eval-calibration-trial-${String(index).padStart(2, "0")}`,
      configId: config.configId,
      role: config.role,
      ...suiteByRole[config.role],
      outputDigest: (index + 1).toString(16).repeat(64).slice(0, 64),
      blindedScore: 9_000 - index,
      spotCheck: { disposition: "pass" as const, failureCodes: [] },
    })),
    selectedConfigs: [
      { role: "prompt_author", configId: "eval-config-prompt-a", selectionDisposition: "approved" },
      { role: "annotator", configId: "eval-config-annotator-a", selectionDisposition: "approved" },
      { role: "adjudicator", configId: "eval-config-adjudicator-a", selectionDisposition: "approved" },
      { role: "final_critic", configId: "eval-config-critic-a", selectionDisposition: "approved" },
    ],
  };
  const calibrationResultDigest = digestStageACalibrationResult(
    calibrationResult,
    calibrationArtifact,
    calibrationInputDigest,
  );
  const filters = Array.from({ length: 20 }, (_, index) => ({
    schemaVersion: STAGE_A_FILTER_SCHEMA_VERSION,
    filterId: `eval-filter-${String(index).padStart(2, "0")}`,
    source: "production_deidentified" as const,
    generalizedContext: {
      companyScope: index % 2 ? "selected" as const : "any" as const,
      locationScope: ["none", "single", "multiple", "global"][index % 4] as "none" | "single" | "multiple" | "global",
      occupationScope: ["none", "single", "multiple"][index % 3] as "none" | "single" | "multiple",
      keywordScope: ["none", "single", "multiple"][(index + 1) % 3] as "none" | "single" | "multiple",
      seniorityScope: ["none", "single", "multiple"][(index + 2) % 3] as "none" | "single" | "multiple",
      technologyScope: ["none", "single", "multiple"][index % 3] as "none" | "single" | "multiple",
      workModeScope: ["none", "single", "multiple"][(index + 1) % 3] as "none" | "single" | "multiple",
      employmentTypeScope: ["none", "single", "multiple"][(index + 2) % 3] as "none" | "single" | "multiple",
      compensationScope: ["none", "minimum", "maximum", "range"][index % 4] as "none" | "minimum" | "maximum" | "range",
      experienceScope: ["none", "minimum", "maximum", "range"][(index + 1) % 4] as "none" | "minimum" | "maximum" | "range",
      locale: ["de", "en", "fr", "it", "other"][index % 5] as "de" | "en" | "fr" | "it" | "other",
    },
  }));
  const personas = ["lazy", "verbose", "misunderstood_purpose", "precise", "vague", "contradictory", "multilingual"] as const;
  const locales = ["de", "en", "fr", "it"] as const;
  const bundles = Array.from({ length: 25 }, (_, index) => {
    const cohortIndex = index < 15 ? index : index - 15;
    return {
      schemaVersion: STAGE_A_BUNDLE_SCHEMA_VERSION,
      bundleId: `eval-bundle-${String(index).padStart(2, "0")}`,
      filterId: `eval-filter-${String(index < 15 ? index : 15 + ((index - 15) % 5)).padStart(2, "0")}`,
      cohort: index < 15 ? "production_shaped" as const : "challenge" as const,
      persona: personas[cohortIndex % personas.length],
      promptLocale: locales[cohortIndex % locales.length],
      softQuery: `synthetic prompt ${String(index).padStart(2, "0")}`,
      promptProvenance: { origin: "agent_synthetic" as const, authorId: `eval-prompt-author-${String(index).padStart(2, "0")}`, configId: "eval-config-prompt-a" },
    };
  });
  const extractionManifest = {
    schemaVersion: STAGE_A_EXTRACTION_MANIFEST_SCHEMA_VERSION,
    repositoryCommit: "a".repeat(40),
    af1ContractVersion: 1 as const,
    typesense: { collectionAlias: "job_posting" as const, resolvedCollection: "job_posting_v42", snapshotDigest: D("c") },
    compiler: { sourcePath: "apps/web/src/lib/search/watchlist-candidate-query.ts", exportName: "buildWatchlistCandidateSearchParams", sourceDigest: D("d") },
    reader: { sourcePath: "apps/web/src/lib/services/watchlist-matcher.ts", exportName: "readWatchlistCandidates", sourceDigest: D("e") },
    dependencyLockDigest: D("f"),
    compiledQueries: filters.map((filter, index) => ({ filterId: filter.filterId, fingerprint: { scheme: "hmac-sha256-v1" as const, value: index.toString(16).padStart(64, "0") } })),
    query: {
      templateDigest: D("0"), order: "first_seen_at_desc_candidate_id_asc" as const, pageSize: 8 as const,
      requestedStrictLowerBound: "2026-08-02T00:00:00.000Z", effectiveWindowStart: "2026-08-02T00:00:01.000Z", cutoff: "2026-09-01T00:00:00.000Z",
      requestedBoundary: "(requestedStrictLowerBound,cutoff)" as const, effectiveBoundary: "[effectiveWindowStart,cutoff)" as const,
      productionSelection: "first_eight" as const, challengeSelection: "frozen_source_rank" as const,
    },
    classifierNormalizer: { sourcePath: "apps/web/src/lib/ai-filter/classifier-input.ts", exportName: "normalizeClassifierInputV1", sourceDigest: D("1"), version: CLASSIFIER_INPUT_NORMALIZER_VERSION, fallbackPolicyDigest: D("2"), truncationPolicyDigest: D("3") },
    softQueryNormalizer: { sourcePath: "apps/web/src/lib/ai-filter/contract.ts", exportName: "normalizeAiFilterSoftQueryV1", sourceDigest: D("4"), version: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION, fallbackPolicyDigest: D("5"), truncationPolicyDigest: D("6") },
  };
  const extractionManifestDigest = digestStageAExtractionManifest(extractionManifest);
  const compiledQueryByFilterId = new Map(extractionManifest.compiledQueries.map(({ filterId, fingerprint }) => [filterId, fingerprint.value]));
  const reviewPlan = { promptReviewSeed: D("c"), auditSeed: D("d"), auditRule: "bounded-16-8-8-v2" as const, auditSize: 32 as const };
  const prePairs = bundles.flatMap((bundle, bundleIndex) => Array.from({ length: 8 }, (_, position) => {
    const index = bundleIndex * 8 + position;
    const cohortIndex = (bundleIndex < 15 ? bundleIndex : bundleIndex - 15) * 8 + position;
    const classifierSource = source(index);
    const contentIdentity = normalizeClassifierInputV1(classifierSource).contentIdentity;
    const postingFirstSeenAt = new Date(Date.UTC(2026, 7, 31, 23, 59, 59 - position)).toISOString();
    const sourceRank = bundle.cohort === "production_shaped" ? position : position * 2;
    const evidenceCondition = cohortIndex < 4 ? "policy_boundary" as const
      : cohortIndex < 8 ? "direct_conflict" as const
        : (["direct_support", "prompt_injection", "insufficient_evidence", "direct_conflict"] as const)[cohortIndex % 4];
    return {
      schemaVersion: STAGE_A_PAIR_SCHEMA_VERSION,
      pairId: `eval-pair-${String(index).padStart(3, "0")}`,
      bundleId: bundle.bundleId,
      position,
      sourceRank,
      postingFirstSeenAt,
      sourceSnapshotIdentity: digestStageASourceSnapshotIdentity({ extractionManifestDigest, compiledQueryFingerprint: compiledQueryByFilterId.get(bundle.filterId)!, candidateId: classifierSource.candidateId, contentIdentity, postingFirstSeenAt, sourceRank }),
      locale: locales[index % locales.length],
      evidenceCondition,
      classifierSource,
      contentIdentity,
    };
  }));
  const preAnnotation: StageAPreAnnotationV2 = {
    schemaVersion: STAGE_A_PRE_ANNOTATION_SCHEMA_VERSION,
    datasetId: "eval-stage-a-synthetic",
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationResultDigest,
    extractionManifest,
    extractionManifestDigest,
    reviewPlan,
    filters,
    bundles,
    pairs: prePairs,
  };
  const preAnnotationDigest = digestStageAPreAnnotation(preAnnotation);
  const promptFeedback: StageAPromptReviewFeedbackV2 = {
    schemaVersion: STAGE_A_PROMPT_REVIEW_FEEDBACK_SCHEMA_VERSION,
    reviewId: "eval-prompt-review",
    reviewerId: "eval-prompt-human",
    preAnnotationDigest,
    approved: true,
    decisions: deriveStageAPromptReviewBundleIds(preAnnotation).map((bundleId) => ({ bundleId, decision: "keep" as const })),
  };
  const promptReviewFeedbackDigest = digestStageAPromptReviewFeedback(promptFeedback, preAnnotation);
  const pairs = prePairs.map((pair, index) => {
    const cohortIndex = (index < 120 ? Math.floor(index / 8) : Math.floor((index - 120) / 8)) * 8 + (index % 8);
    const policyBoundary = cohortIndex < 4;
    const disagreement = cohortIndex >= 4 && cohortIndex < 8;
    const firstLabel = index % 2 ? "reject" as const : "accept" as const;
    const secondLabel = disagreement ? (firstLabel === "accept" ? "reject" as const : "accept" as const) : firstLabel;
    const annotations = [
      { annotationId: `eval-ann-${String(index).padStart(3, "0")}-a`, actorId: "eval-annotator-a", configId: "eval-config-annotator-a", label: firstLabel, ambiguity: false, rationaleCode: "direct_evidence" as const, evidenceRefs: ["description_text" as const] },
      { annotationId: `eval-ann-${String(index).padStart(3, "0")}-b`, actorId: "eval-annotator-b", configId: "eval-config-annotator-a", label: secondLabel, ambiguity: false, rationaleCode: disagreement ? "contradiction" as const : "direct_evidence" as const, evidenceRefs: ["description_text" as const] },
    ] as const;
    return {
      ...pair,
      annotations,
      adjudication: policyBoundary || disagreement ? {
        adjudicationId: `eval-adj-${String(index).padStart(3, "0")}`,
        actorId: "eval-adjudicator",
        configId: "eval-config-adjudicator-a",
        annotationIds: [annotations[0].annotationId, annotations[1].annotationId] as const,
        label: firstLabel,
        ambiguity: false,
        rationaleCode: policyBoundary ? "policy_interpretation" as const : "direct_evidence" as const,
      } : null,
    };
  });
  const wipReviewPayload = {
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: preAnnotation.datasetId,
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationResultDigest,
    preAnnotationDigest,
    promptReviewFeedbackDigest,
    filters,
    bundles,
    pairs,
  };
  const wip: StageAWipV2 = {
    ...wipReviewPayload,
    finalCritic: {
      reviewId: "eval-final-review",
      actorId: "eval-final-critic",
      configId: "eval-config-critic-a",
      reviewedWipDigest: digestStageAWipReviewPayload(wipReviewPayload),
      approved: true,
    },
  };
  const freeze = () => freezeStageASilver(wip, calibrationArtifact, calibrationInputDigest, calibrationResult, calibrationResultDigest, preAnnotation, preAnnotationDigest, promptFeedback, promptReviewFeedbackDigest);
  return { calibrationArtifact, calibrationInputDigest, calibrationResult, calibrationResultDigest, preAnnotation, preAnnotationDigest, promptFeedback, promptReviewFeedbackDigest, wip, freeze };
}

function humanFeedback(sourceSilverDigest: string, auditPolicyDigest: string, pairIds: readonly string[]): StageAHumanFeedbackV2 {
  return { schemaVersion: STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION, feedbackId: "eval-human-feedback", reviewerId: "eval-audit-human", sourceSilverDigest, auditPolicyDigest, approved: true, decisions: pairIds.map((pairId) => ({ pairId, judgment: "accept" })) };
}

async function useTemporaryRoot() {
  const root = await mkdtemp(path.join(tmpdir(), "jobseek-stage-a-v2-"));
  temporaryRoots.push(root);
  setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: root });
  return root;
}

async function writeGoldFixture() {
  const root = await useTemporaryRoot();
  const data = fixture();
  const silver = data.freeze();
  const policy = deriveStageAHumanAuditPolicy(silver, silver.silverDigest);
  const policyDigest = digestStageAHumanAuditPolicy(policy, silver, silver.silverDigest);
  const feedback = humanFeedback(silver.silverDigest, policyDigest, policy.auditPairIds);
  const gold = promoteStageAGold(silver, silver.silverDigest, policy, policyDigest, feedback);
  await writeFile(path.join(root, "gold.json"), canonicalStageAJson(gold), { mode: 0o600 });
  return { root, data, silver, policy, policyDigest, feedback, gold };
}

describe("Stage A v2 gates", () => {
  it("compiles strict schemas for every frozen boundary", () => {
    const ajv = new Ajv({ allErrors: true, strict: true });
    for (const schema of [STAGE_A_FILTER_V2_SCHEMA, STAGE_A_BUNDLE_V2_SCHEMA, STAGE_A_PRE_ANNOTATION_PAIR_V2_SCHEMA, STAGE_A_PAIR_V2_SCHEMA, STAGE_A_CALIBRATION_ARTIFACT_V2_SCHEMA, STAGE_A_CALIBRATION_RESULT_V2_SCHEMA, STAGE_A_PRE_ANNOTATION_V2_SCHEMA, STAGE_A_PROMPT_REVIEW_FEEDBACK_V2_SCHEMA, STAGE_A_WIP_V2_SCHEMA, STAGE_A_SILVER_MANIFEST_V2_SCHEMA, STAGE_A_SILVER_FREEZE_V2_SCHEMA, STAGE_A_HUMAN_AUDIT_POLICY_V2_SCHEMA, STAGE_A_HUMAN_FEEDBACK_V2_SCHEMA, STAGE_A_GOLD_MANIFEST_V2_SCHEMA, STAGE_A_GOLD_FREEZE_V2_SCHEMA]) ajv.addSchema(schema);
    const data = fixture();
    expect(ajv.getSchema(STAGE_A_CALIBRATION_ARTIFACT_V2_SCHEMA.$id)?.(data.calibrationArtifact)).toBe(true);
    expect(ajv.getSchema(STAGE_A_CALIBRATION_RESULT_V2_SCHEMA.$id)?.(data.calibrationResult)).toBe(true);
    expect(ajv.getSchema(STAGE_A_PRE_ANNOTATION_V2_SCHEMA.$id)?.(data.preAnnotation)).toBe(true);
    expect(ajv.getSchema(STAGE_A_WIP_V2_SCHEMA.$id)?.(data.wip)).toBe(true);
  });

  it("enforces the disjoint 15 production plus five twice-challenge filter mapping", () => {
    const data = fixture();
    expect(validateStageAWip(data.wip).pairs).toHaveLength(200);
    const wrong = clone(data.wip);
    wrong.bundles[15].filterId = wrong.bundles[0].filterId;
    expect(() => validateStageAWip(wrong)).toThrow(/disjoint_15_plus_5_filter_mapping_required/u);
    const duplicateFingerprint = clone(data.preAnnotation);
    duplicateFingerprint.extractionManifest.compiledQueries[1].fingerprint.value = duplicateFingerprint.extractionManifest.compiledQueries[0].fingerprint.value;
    expect(() => digestStageAPreAnnotation(duplicateFingerprint)).toThrow(/unique_compiled_query_fingerprints_required/u);
  });

  it("requires blind labels with ambiguity, evidence, linked adjudication, and role separation", () => {
    const data = fixture();
    const noEvidence = clone(data.wip);
    noEvidence.pairs[20].annotations[0].evidenceRefs = [];
    expect(() => validateStageAWip(noEvidence)).toThrow(/bounded_array_required/u);
    const ambiguous = clone(data.wip);
    ambiguous.pairs[20].annotations[0].ambiguity = true;
    expect(() => validateStageAWip(ambiguous)).toThrow(/adjudication_required/u);
    const badLink = clone(data.wip);
    badLink.pairs[0].adjudication!.annotationIds[0] = "eval-ann-unrelated";
    expect(() => validateStageAWip(badLink)).toThrow(/annotation_linkage_required/u);
    const roleConflict = clone(data.wip);
    roleConflict.pairs[20].annotations[0].actorId = roleConflict.bundles[2].promptProvenance.authorId;
    expect(() => validateStageAWip(roleConflict)).toThrow(/global_role_separation_required/u);
  });

  it("pins pre-annotation feed order and prevents post-review input drift", () => {
    const data = fixture();
    const reordered = clone(data.preAnnotation);
    reordered.pairs[0].sourceRank = 1;
    expect(() => digestStageAPreAnnotation(reordered)).toThrow(/production_must_use_first_eight_required/u);
    const drifted = clone(data.wip);
    drifted.pairs[20].classifierSource.title = "changed after prompt review";
    drifted.pairs[20].contentIdentity = normalizeClassifierInputV1(drifted.pairs[20].classifierSource).contentIdentity;
    expect(() => freezeStageASilver(drifted, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest)).toThrow(/final_critic_review_pin_mismatch/u);
    reapprove(drifted);
    expect(() => freezeStageASilver(drifted, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest)).toThrow(/pre_annotation_projection_mismatch/u);
  });

  it("translates AF-1's strict lower bound to an exact whole-second effective window", () => {
    const data = fixture();
    const excluded = clone(data.preAnnotation);
    excluded.pairs[7].postingFirstSeenAt = excluded.extractionManifest.query.requestedStrictLowerBound;
    expect(() => digestStageAPreAnnotation(excluded)).toThrow(/selection_window_required/u);

    const included = clone(data.preAnnotation);
    const pair = included.pairs[7];
    pair.postingFirstSeenAt = included.extractionManifest.query.effectiveWindowStart;
    pair.sourceSnapshotIdentity = digestStageASourceSnapshotIdentity({
      extractionManifestDigest: included.extractionManifestDigest,
      compiledQueryFingerprint: included.extractionManifest.compiledQueries.find(({ filterId }) => filterId === included.bundles[0].filterId)!.fingerprint.value,
      candidateId: pair.classifierSource.candidateId,
      contentIdentity: pair.contentIdentity,
      postingFirstSeenAt: pair.postingFirstSeenAt,
      sourceRank: pair.sourceRank,
    });
    expect(digestStageAPreAnnotation(included)).toMatch(/^[a-f0-9]{64}$/u);

    const translatedWrong = clone(data.preAnnotation);
    translatedWrong.extractionManifest.query.effectiveWindowStart = "2026-08-02T00:00:02.000Z";
    expect(() => digestStageAPreAnnotation(translatedWrong)).toThrow(/strict_lower_bound_translation_required/u);
  });

  it("pins the extraction manifest into every source snapshot identity", () => {
    const data = fixture();
    const unpinnedManifest = clone(data.preAnnotation);
    unpinnedManifest.extractionManifest.compiler.sourceDigest = D("a");
    expect(() => digestStageAPreAnnotation(unpinnedManifest)).toThrow(/extraction_manifest_digest_mismatch/u);

    const repinnedManifest = clone(data.preAnnotation);
    repinnedManifest.extractionManifest.compiler.sourceDigest = D("a");
    repinnedManifest.extractionManifestDigest = digestStageAExtractionManifest(repinnedManifest.extractionManifest);
    expect(() => digestStageAPreAnnotation(repinnedManifest)).toThrow(/source_snapshot_identity_mismatch/u);
  });

  it("requires an approved exact 12-card pre-annotation prompt gate", () => {
    const data = fixture();
    expect(deriveStageAPromptReviewBundleIds(data.preAnnotation)).toHaveLength(12);
    const revised = clone(data.promptFeedback);
    revised.decisions[0].decision = "revise";
    const revisedDigest = digestStageAPromptReviewFeedback(revised, data.preAnnotation);
    expect(() => freezeStageASilver(data.wip, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, revised, revisedDigest)).toThrow(/prompt_review_approval_required/u);
  });

  it("resolves every agent output to the approved role configuration", () => {
    const data = fixture();
    const drift = clone(data.wip);
    drift.pairs[20].annotations[0].configId = "eval-config-prompt";
    reapprove(drift);
    expect(() => freezeStageASilver(drift, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest)).toThrow(/selected_role_config_required/u);
    expect(() => freezeStageASilver(data.wip, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, D("0"), data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest)).toThrow(/calibration_result_pin_mismatch/u);
    const unreviewedExample = clone(data.calibrationResult);
    unreviewedExample.humanReview.decisions[0].calibrationExampleId = "eval-calibration-example-missing";
    const changedGroundTruthDigest = digestStageAResolvedCalibrationGroundTruth(unreviewedExample.humanReview.decisions);
    for (const trial of unreviewedExample.trials.filter(({ role }) => role === "annotator")) trial.suiteInputDigest = changedGroundTruthDigest;
    expect(() => digestStageACalibrationResult(unreviewedExample, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/complete_calibration_decisions_required/u);
  });

  it("requires exercised, role-specific passing calibration candidates before selection", () => {
    const data = fixture();
    expect(data.calibrationResult.humanReview.decisions.at(-1)?.judgment).toBe("unclear");
    expect(digestStageACalibrationResult(data.calibrationResult, data.calibrationArtifact, data.calibrationInputDigest)).toBe(data.calibrationResultDigest);

    const tooFewResolved = clone(data.calibrationResult);
    tooFewResolved.humanReview.decisions[0].judgment = "unclear";
    expect(() => digestStageACalibrationResult(tooFewResolved, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/minimum_resolved_calibration_decisions_required/u);

    const onePromptCandidate = clone(data.calibrationResult);
    onePromptCandidate.candidateConfigs[1].role = "annotator";
    expect(() => digestStageACalibrationResult(onePromptCandidate, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/two_candidate_configs_per_role_required/u);

    const wrongSuite = clone(data.calibrationResult);
    wrongSuite.trials[0].suiteKind = "seeded_defects";
    expect(() => digestStageACalibrationResult(wrongSuite, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/role_specific_calibration_suite_required/u);

    for (const role of ["adjudicator", "final_critic"] as const) {
      const undersizedSeededSuite = clone(data.calibrationResult);
      for (const trial of undersizedSeededSuite.trials.filter((candidateTrial) => candidateTrial.role === role)) trial.sampleCount = 7;
      expect(() => digestStageACalibrationResult(undersizedSeededSuite, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/seeded_suite_minimum_required/u);
    }

    const inconsistentSuiteCount = clone(data.calibrationResult);
    inconsistentSuiteCount.trials.find(({ configId }) => configId === "eval-config-critic-b")!.sampleCount = 9;
    expect(() => digestStageACalibrationResult(inconsistentSuiteCount, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/same_role_suite_sample_count_required/u);

    const unexercised = clone(data.calibrationResult);
    unexercised.trials[0].configId = "eval-config-not-a-candidate";
    expect(() => digestStageACalibrationResult(unexercised, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/every_candidate_config_must_be_exercised/u);

    const failedSelection = clone(data.calibrationResult);
    const selectedId = failedSelection.selectedConfigs[0].configId;
    const selectedTrial = failedSelection.trials.find(({ configId }) => configId === selectedId)!;
    selectedTrial.spotCheck.disposition = "fail";
    selectedTrial.spotCheck.failureCodes = ["seeded-defect-missed"];
    expect(() => digestStageACalibrationResult(failedSelection, data.calibrationArtifact, data.calibrationInputDigest)).toThrow(/selected_candidate_passing_trial_required/u);
  });

  it("derives the bounded 16/8/8 audit after silver and rejects arbitrary selection", () => {
    const data = fixture();
    const silver = data.freeze();
    const policy = deriveStageAHumanAuditPolicy(silver, silver.silverDigest);
    expect(policy.auditPairIds).toHaveLength(32);
    const selected = silver.manifest.pairs.filter(({ pairId }) => policy.auditPairIds.includes(pairId));
    const ambiguousPolicy = selected.filter((pair) => pair.evidenceCondition === "policy_boundary" || pair.annotations.some(({ ambiguity }) => ambiguity) || pair.finalAmbiguity);
    const disagreement = selected.filter((pair) => !ambiguousPolicy.includes(pair) && pair.annotations[0].label !== pair.annotations[1].label);
    const agreements = selected.filter((pair) => !ambiguousPolicy.includes(pair) && !disagreement.includes(pair));
    expect(ambiguousPolicy).toHaveLength(8);
    expect(disagreement).toHaveLength(8);
    const cohortByBundle = new Map(silver.manifest.bundles.map(({ bundleId, cohort }) => [bundleId, cohort]));
    expect(agreements.filter(({ bundleId }) => cohortByBundle.get(bundleId) === "production_shaped")).toHaveLength(8);
    expect(agreements.filter(({ bundleId }) => cohortByBundle.get(bundleId) === "challenge")).toHaveLength(8);
    const arbitrary = clone(policy);
    arbitrary.auditPairIds[0] = silver.manifest.pairs.find(({ pairId }) => !policy.auditPairIds.includes(pairId))!.pairId;
    expect(() => digestStageAHumanAuditPolicy(arbitrary, silver, silver.silverDigest)).toThrow(/derived_audit_policy_required/u);

    const backfillWip = clone(data.wip);
    backfillWip.pairs[4].annotations[1].label = backfillWip.pairs[4].annotations[0].label;
    backfillWip.pairs[4].adjudication = null;
    reapprove(backfillWip);
    const backfillSilver = freezeStageASilver(backfillWip, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest);
    const backfillPolicy = deriveStageAHumanAuditPolicy(backfillSilver, backfillSilver.silverDigest);
    const backfillSelected = backfillSilver.manifest.pairs.filter(({ pairId }) => backfillPolicy.auditPairIds.includes(pairId));
    const backfillAgreements = backfillSelected.filter((pair) => pair.evidenceCondition !== "policy_boundary" && !pair.annotations.some(({ ambiguity }) => ambiguity) && !pair.finalAmbiguity && pair.annotations[0].label === pair.annotations[1].label);
    const productionAgreementCount = backfillAgreements.filter(({ bundleId }) => cohortByBundle.get(bundleId) === "production_shaped").length;
    expect(backfillAgreements).toHaveLength(17);
    expect(Math.abs(productionAgreementCount - (backfillAgreements.length - productionAgreementCount))).toBeLessThanOrEqual(1);
  });

  it("renders exactly 12 pre-annotation prompt cards and 32 blind audit cards", () => {
    const data = fixture();
    const promptPacket = renderStageAPromptReviewPacket(data.preAnnotation);
    expect(promptPacket.match(/^## /gmu)).toHaveLength(12);
    expect(promptPacket).toContain("Prompt locale:");
    expect(promptPacket).not.toMatch(/annotationId|silverLabel|configId|compiledQueryFingerprint|candidateId/u);
    const silver = data.freeze();
    const auditPacket = renderStageALabelAuditPacket(silver, silver.silverDigest);
    expect(auditPacket.match(/^## /gmu)).toHaveLength(32);
    expect(auditPacket).not.toMatch(/silverLabel|annotationId|actorId|configId|ambiguity|production_shaped|challenge|Cohort:/u);
  });

  it("fails the fleet-quality gate when bounded disagreement volume is exceeded", () => {
    const data = fixture();
    const expanded = clone(data.wip);
    const pair = expanded.pairs[20];
    pair.annotations[1].label = pair.annotations[0].label === "accept" ? "reject" : "accept";
    pair.adjudication = { adjudicationId: "eval-adj-extra", actorId: "eval-adjudicator", configId: "eval-config-adjudicator-a", annotationIds: [pair.annotations[0].annotationId, pair.annotations[1].annotationId], label: pair.annotations[0].label, ambiguity: false, rationaleCode: "direct_evidence" };
    reapprove(expanded);
    const silver = freezeStageASilver(expanded, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest);
    expect(() => deriveStageAHumanAuditPolicy(silver, silver.silverDigest)).toThrow(/fleet_quality_audit_quota_exceeded/u);
  });

  it("does not invoke accessors or proxy traps during canonicalization", () => {
    let getterCalled = false;
    const withGetter = Object.defineProperty({}, "secret", { enumerable: true, get() { getterCalled = true; throw new Error("secret-value"); } });
    expect(() => canonicalStageAJson(withGetter)).toThrow(/canonical_data_property_required/u);
    expect(getterCalled).toBe(false);
    let trapCalled = false;
    const proxy = new Proxy({}, { ownKeys() { trapCalled = true; throw new Error("secret-proxy"); } });
    expect(() => canonicalStageAJson(proxy)).toThrow(/proxy_forbidden/u);
    expect(trapCalled).toBe(false);
  });

  it("promotes complete approved feedback and keeps target inputs metadata-free", async () => {
    const { data, silver, policy, policyDigest, gold } = await writeGoldFixture();
    expect(gold.manifest.pairs.filter(({ goldProvenance }) => goldProvenance.source === "human_reviewed")).toHaveLength(32);
    const args = ["gold.json", gold.goldDigest, silver.silverDigest, policyDigest, data.calibrationResultDigest] as const;
    const targets = await loadStageATargetInputs(...args);
    const labels = await loadStageAScoringLabels(...args);
    expect(Object.keys(targets[0])).toEqual(["pairId", "query", "classifierInput"]);
    expect(canonicalStageAJson(targets)).not.toMatch(/goldLabel|silverLabel|persona|cohort|actorId|configId|ambiguity/u);
    expect(labels).toHaveLength(200);
    const incomplete = humanFeedback(silver.silverDigest, policyDigest, policy.auditPairIds.slice(1));
    expect(() => promoteStageAGold(silver, silver.silverDigest, policy, policyDigest, incomplete)).toThrow(/bounded_array_required/u);
  });

  it("writes immutable private freezes and reports cohorts separately", async () => {
    const root = await useTemporaryRoot();
    const data = fixture();
    const result = await writeStageASilverFreezeFile("silver.json", data.wip, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest);
    expect(result.silverDigest).toMatch(/^[a-f0-9]{64}$/u);
    expect((await stat(path.join(root, "silver.json"))).mode & 0o077).toBe(0);
    await expect(writeStageASilverFreezeFile("silver.json", data.wip, data.calibrationArtifact, data.calibrationInputDigest, data.calibrationResult, data.calibrationResultDigest, data.preAnnotation, data.preAnnotationDigest, data.promptFeedback, data.promptReviewFeedbackDigest)).rejects.toEqual(new StageAEvaluationError("$file", "exclusive_publish_failed"));
    await rm(root, { recursive: true, force: true });
    temporaryRoots.pop();
    const saved = await writeGoldFixture();
    const report = await reportStageAGoldFreeze("gold.json", saved.gold.goldDigest, saved.silver.silverDigest, saved.policyDigest, saved.data.calibrationResultDigest);
    expect(report.cohorts.production_shaped.totalPairs).toBe(120);
    expect(report.cohorts.challenge.totalPairs).toBe(80);
  });

  it("stores calibration decisions externally and pins only their approved result digest", async () => {
    const { root, data } = await writeGoldFixture();
    const bytes = await readFile(path.join(root, "gold.json"), "utf8");
    expect(bytes).toContain(`"calibrationResultDigest":"${data.calibrationResultDigest}"`);
    expect(bytes).not.toContain("calibrationExampleId");
  });
});
