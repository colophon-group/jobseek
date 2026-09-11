import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { restoreTestEnv, setTestEnv, snapshotTestEnv } from "@/test-utils/env";
import Ajv from "ajv";
import { afterEach, describe, expect, it } from "vitest";
import {
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
  normalizeClassifierInputV1,
} from "../classifier-input";
import { AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION } from "../contract";
import {
  STAGE_A_BUNDLE_V2_SCHEMA,
  STAGE_A_FILTER_V2_SCHEMA,
  STAGE_A_GOLD_FREEZE_V2_SCHEMA,
  STAGE_A_GOLD_MANIFEST_V2_SCHEMA,
  STAGE_A_HUMAN_AUDIT_POLICY_V2_SCHEMA,
  STAGE_A_HUMAN_FEEDBACK_V2_SCHEMA,
  STAGE_A_PAIR_V2_SCHEMA,
  STAGE_A_SILVER_FREEZE_V2_SCHEMA,
  STAGE_A_SILVER_MANIFEST_V2_SCHEMA,
  STAGE_A_WIP_V2_SCHEMA,
} from "./schemas";
import {
  STAGE_A_BUNDLE_SCHEMA_VERSION,
  STAGE_A_FILTER_SCHEMA_VERSION,
  STAGE_A_GOLD_FREEZE_SCHEMA_VERSION,
  STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION,
  STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION,
  STAGE_A_PAIR_SCHEMA_VERSION,
  STAGE_A_WIP_SCHEMA_VERSION,
  StageAEvaluationError,
  canonicalStageAJson,
  digestStageAHumanAuditPolicy,
  freezeStageASilver,
  loadStageAScoringLabels,
  loadStageATargetInputs,
  promoteStageAGold,
  readStageAHumanAuditPolicyFile,
  reportStageAGoldFreeze,
  validateStageAWip,
  writeStageASilverFreezeFile,
  type StageAHumanAuditPolicyV2,
  type StageAHumanFeedbackV2,
  type StageAWipV2,
} from "./stage-a";

const temporaryRoots: string[] = [];
const originalEnv = snapshotTestEnv(["AI_FILTER_EVAL_DATA_ROOT"]);
const CALIBRATION_DIGEST = "c".repeat(64);

afterEach(async () => {
  restoreTestEnv(originalEnv);
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

type Mutable<T> = T extends readonly (infer Item)[]
  ? Mutable<Item>[]
  : T extends object
    ? { -readonly [Key in keyof T]: Mutable<T[Key]> }
    : T;

function clone<T>(input: T): Mutable<T> {
  return JSON.parse(JSON.stringify(input)) as Mutable<T>;
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

function syntheticWip(): StageAWipV2 {
  const filters = Array.from({ length: 20 }, (_, index) => ({
    schemaVersion: STAGE_A_FILTER_SCHEMA_VERSION,
    filterId: `eval-filter-${String(index).padStart(2, "0")}`,
    source: "production_deidentified" as const,
    sourceFilterDigest: index.toString(16).padStart(64, "0"),
    generalizedContext: {
      companyScope: index % 2 === 0 ? "any" as const : "selected" as const,
      locationScope: ["none", "single", "multiple", "global"] as const,
      occupationScope: ["none", "single", "multiple"] as const,
      keywordScope: ["none", "single", "multiple"] as const,
      seniorityScope: ["none", "single", "multiple"] as const,
      technologyScope: ["none", "single", "multiple"] as const,
      workModeScope: ["none", "single", "multiple"] as const,
      employmentTypeScope: ["none", "single", "multiple"] as const,
      compensationScope: ["none", "minimum", "maximum", "range"] as const,
      experienceScope: ["none", "minimum", "maximum", "range"] as const,
      locale: ["de", "en", "fr", "it", "other"] as const,
    },
  })).map((filter, index) => ({
    ...filter,
    generalizedContext: {
      companyScope: filter.generalizedContext.companyScope,
      locationScope: filter.generalizedContext.locationScope[index % 4],
      occupationScope: filter.generalizedContext.occupationScope[index % 3],
      keywordScope: filter.generalizedContext.keywordScope[(index + 1) % 3],
      seniorityScope: filter.generalizedContext.seniorityScope[(index + 2) % 3],
      technologyScope: filter.generalizedContext.technologyScope[index % 3],
      workModeScope: filter.generalizedContext.workModeScope[(index + 1) % 3],
      employmentTypeScope: filter.generalizedContext.employmentTypeScope[(index + 2) % 3],
      compensationScope: filter.generalizedContext.compensationScope[index % 4],
      experienceScope: filter.generalizedContext.experienceScope[(index + 1) % 4],
      locale: filter.generalizedContext.locale[index % 5],
    },
  }));
  const bundles = Array.from({ length: 25 }, (_, index) => ({
    schemaVersion: STAGE_A_BUNDLE_SCHEMA_VERSION,
    bundleId: `eval-bundle-${String(index).padStart(2, "0")}`,
    filterId: `eval-filter-${String(index < 20 ? index : index - 20).padStart(2, "0")}`,
    cohort: index < 15 ? "production_shaped" as const : "challenge" as const,
    persona: ["lazy", "verbose", "misunderstood_purpose", "precise", "vague", "contradictory", "multilingual"] as const,
    softQuery: `synthetic prompt ${String(index).padStart(2, "0")}`,
    promptProvenance: {
      origin: "agent_synthetic" as const,
      authorId: `eval-author-${String(index).padStart(2, "0")}`,
      agentRole: "jobseek-prompt-author",
      model: "gpt-5.6-terra",
      modelVersion: "2026-09",
      reasoningEffort: ["low", "medium", "high"] as const,
      taskPromptDigest: (index + 100).toString(16).padStart(64, "0"),
    },
  })).map((bundle, index) => ({
    ...bundle,
    persona: bundle.persona[index % bundle.persona.length],
    promptProvenance: {
      ...bundle.promptProvenance,
      reasoningEffort: bundle.promptProvenance.reasoningEffort[index % 3],
    },
  }));
  const pairs = bundles.flatMap((bundle, bundleIndex) => Array.from({ length: 8 }, (_, localIndex) => {
    const index = bundleIndex * 8 + localIndex;
    const classifierSource = source(index);
    const ambiguity = localIndex === 0 || localIndex === 1;
    const evidenceCondition = localIndex === 0
      ? "policy_boundary" as const
      : localIndex === 1
        ? "insufficient_evidence" as const
        : localIndex === 2
          ? "prompt_injection" as const
          : localIndex % 2 === 0
            ? "direct_support" as const
            : "direct_conflict" as const;
    const firstLabel = localIndex % 2 === 0 ? "accept" as const : "reject" as const;
    const secondLabel = localIndex === 0 ? "reject" as const : firstLabel;
    const adjudicationRequired = ambiguity || evidenceCondition === "policy_boundary" || firstLabel !== secondLabel;
    return {
      schemaVersion: STAGE_A_PAIR_SCHEMA_VERSION,
      pairId: `eval-pair-${String(index).padStart(3, "0")}`,
      bundleId: bundle.bundleId,
      locale: ["de", "en", "fr", "it"][index % 4] as "de" | "en" | "fr" | "it",
      evidenceCondition,
      ambiguity,
      classifierSource,
      contentIdentity: normalizeClassifierInputV1(classifierSource).contentIdentity,
      annotations: [
        { annotationId: `eval-annotation-${String(index).padStart(3, "0")}-a`, actorId: "eval-annotator-a", label: firstLabel },
        { annotationId: `eval-annotation-${String(index).padStart(3, "0")}-b`, actorId: "eval-annotator-b", label: secondLabel },
      ] as const,
      adjudication: adjudicationRequired
        ? { adjudicationId: `eval-adjudication-${String(index).padStart(3, "0")}`, actorId: "eval-adjudicator", label: firstLabel }
        : null,
    };
  }));
  return {
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: "eval-stage-a-synthetic",
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    softQueryNormalizerVersion: AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION,
    calibrationDigest: CALIBRATION_DIGEST,
    filters,
    bundles,
    pairs,
    finalCritic: { reviewId: "eval-final-review", actorId: "eval-final-critic", approved: true },
  };
}

function auditPolicy(silverDigest: string, count = 24): StageAHumanAuditPolicyV2 {
  return {
    schemaVersion: STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION,
    sourceSilverDigest: silverDigest,
    auditPairIds: Array.from({ length: count }, (_, index) => `eval-pair-${String(index).padStart(3, "0")}`),
  };
}

function feedback(policy: StageAHumanAuditPolicyV2, policyDigest: string): StageAHumanFeedbackV2 {
  return {
    schemaVersion: STAGE_A_HUMAN_FEEDBACK_SCHEMA_VERSION,
    feedbackId: "eval-human-feedback",
    reviewerId: "eval-human-reviewer",
    sourceSilverDigest: policy.sourceSilverDigest,
    auditPolicyDigest: policyDigest,
    approved: true,
    decisions: policy.auditPairIds.map((pairId, index) => ({
      pairId,
      judgment: index === 0 ? "reject" : index % 2 === 0 ? "accept" : "reject",
    })),
  };
}

async function useTemporaryRoot() {
  const root = await mkdtemp(path.join(tmpdir(), "jobseek-stage-a-v2-"));
  temporaryRoots.push(root);
  setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: root });
  return root;
}

async function writeGoldFixture() {
  const root = await useTemporaryRoot();
  const silver = freezeStageASilver(syntheticWip(), CALIBRATION_DIGEST);
  const policy = auditPolicy(silver.silverDigest);
  const policyDigest = digestStageAHumanAuditPolicy(policy);
  const humanFeedback = feedback(policy, policyDigest);
  const gold = promoteStageAGold(silver, silver.silverDigest, CALIBRATION_DIGEST, policy, policyDigest, humanFeedback);
  await writeFile(path.join(root, "gold.json"), canonicalStageAJson(gold), { mode: 0o600 });
  return { root, silver, policy, policyDigest, humanFeedback, gold };
}

describe("Stage A v2 corpus contract", () => {
  it("publishes strict v2-only schemas that compile", () => {
    const ajv = new Ajv({ allErrors: true, strict: true });
    for (const schema of [
      STAGE_A_FILTER_V2_SCHEMA,
      STAGE_A_BUNDLE_V2_SCHEMA,
      STAGE_A_PAIR_V2_SCHEMA,
      STAGE_A_WIP_V2_SCHEMA,
      STAGE_A_SILVER_MANIFEST_V2_SCHEMA,
      STAGE_A_SILVER_FREEZE_V2_SCHEMA,
      STAGE_A_HUMAN_AUDIT_POLICY_V2_SCHEMA,
      STAGE_A_HUMAN_FEEDBACK_V2_SCHEMA,
      STAGE_A_GOLD_MANIFEST_V2_SCHEMA,
      STAGE_A_GOLD_FREEZE_V2_SCHEMA,
    ]) ajv.addSchema(schema);
    expect(ajv.getSchema(STAGE_A_WIP_V2_SCHEMA.$id)?.(syntheticWip())).toBe(true);
    expect(JSON.stringify(STAGE_A_WIP_V2_SCHEMA)).not.toContain("stage-a-wip-v1");
  });

  it("enforces the exact 20 filters, 25 bundles, 120/80 pairs and five reuses", () => {
    const validated = validateStageAWip(syntheticWip());
    expect(validated.filters).toHaveLength(20);
    expect(validated.bundles).toHaveLength(25);
    expect(validated.pairs).toHaveLength(200);
    const cohortByBundle = new Map(validated.bundles.map(({ bundleId, cohort }) => [bundleId, cohort]));
    expect(validated.pairs.filter(({ bundleId }) => cohortByBundle.get(bundleId) === "production_shaped")).toHaveLength(120);
    expect(validated.pairs.filter(({ bundleId }) => cohortByBundle.get(bundleId) === "challenge")).toHaveLength(80);
    expect(validated.filters.filter(({ filterId }) => validated.bundles.filter((bundle) => bundle.filterId === filterId).length === 2)).toHaveLength(5);
  });

  it("rejects under-labelled, unadjudicated and role-conflicted work", () => {
    const oneLabel = clone(syntheticWip());
    oneLabel.pairs[3].annotations.pop();
    expect(() => validateStageAWip(oneLabel)).toThrow(/bounded_array_required/u);

    const sameAnnotator = clone(syntheticWip());
    sameAnnotator.pairs[3].annotations[1].actorId = sameAnnotator.pairs[3].annotations[0].actorId;
    expect(() => validateStageAWip(sameAnnotator)).toThrow(/blind_distinct_annotators_required/u);

    const missingAdjudication = clone(syntheticWip());
    missingAdjudication.pairs[0].adjudication = null;
    expect(() => validateStageAWip(missingAdjudication)).toThrow(/adjudication_required/u);

    const authorAnnotates = clone(syntheticWip());
    authorAnnotates.pairs[0].annotations[0].actorId = authorAnnotates.bundles[0].promptProvenance.authorId;
    expect(() => validateStageAWip(authorAnnotates)).toThrow(/global_role_separation_required/u);

    const criticParticipates = clone(syntheticWip());
    criticParticipates.finalCritic.actorId = "eval-annotator-a";
    expect(() => validateStageAWip(criticParticipates)).toThrow(/independent_final_critic_required/u);
  });

  it("rejects target predictions, blended fields, bad provenance and shape drift", () => {
    const prediction = clone(syntheticWip()) as Mutable<StageAWipV2> & { pairs: Array<Record<string, unknown>> };
    prediction.pairs[0].targetPrediction = "accept";
    expect(() => validateStageAWip(prediction)).toThrow(/additional_properties/u);

    const blended = clone(syntheticWip()) as Mutable<StageAWipV2> & { pairs: Array<Record<string, unknown>> };
    blended.pairs[0].cohort = "challenge";
    expect(() => validateStageAWip(blended)).toThrow(/additional_properties/u);

    const noModel = clone(syntheticWip()) as Mutable<StageAWipV2>;
    delete (noModel.bundles[0].promptProvenance as Partial<typeof noModel.bundles[0]["promptProvenance"]>).modelVersion;
    expect(() => validateStageAWip(noModel)).toThrow(/required/u);

    const wrongSplit = clone(syntheticWip());
    wrongSplit.bundles[14].cohort = "challenge";
    expect(() => validateStageAWip(wrongSplit)).toThrow(/exact_cohort_split_required/u);

    const wrongReuse = clone(syntheticWip());
    wrongReuse.bundles[24].filterId = wrongReuse.bundles[23].filterId;
    expect(() => validateStageAWip(wrongReuse)).toThrow(/five_extra_filter_uses_required/u);
  });

  it("normalizes classifier inputs and requires the external calibration pin", () => {
    const input = clone(syntheticWip());
    input.pairs[0].classifierSource.title = "  Synthetic   role  ";
    input.pairs[0].contentIdentity = normalizeClassifierInputV1(input.pairs[0].classifierSource).contentIdentity;
    const frozen = freezeStageASilver(input, CALIBRATION_DIGEST);
    expect(frozen.manifest.pairs[0].classifierInput.title).toBe("Synthetic role");
    expect(() => freezeStageASilver(input, "0".repeat(64))).toThrow(/calibration_digest_mismatch/u);
  });

  it("freezes immutable silver with row provenance and no target predictions", () => {
    const frozen = freezeStageASilver(syntheticWip(), CALIBRATION_DIGEST);
    expect(frozen.manifest.status).toBe("agent_adjudicated_silver");
    expect(frozen.manifest.pairs.every(({ annotations }) => annotations.length === 2)).toBe(true);
    expect(frozen.manifest.pairs[0].silverProvenance.method).toBe("adjudication");
    expect(frozen.manifest.pairs[3].silverProvenance.method).toBe("agreement");
    expect(canonicalStageAJson(frozen)).not.toContain("targetPrediction");
    expect(Object.isFrozen(frozen.manifest.pairs)).toBe(true);
  });

  it("promotes only a digest-pinned, complete, explicitly approved audit", () => {
    const silver = freezeStageASilver(syntheticWip(), CALIBRATION_DIGEST);
    const policy = auditPolicy(silver.silverDigest, 32);
    const policyDigest = digestStageAHumanAuditPolicy(policy);
    const humanFeedback = feedback(policy, policyDigest);
    const gold = promoteStageAGold(silver, silver.silverDigest, CALIBRATION_DIGEST, policy, policyDigest, humanFeedback);
    expect(gold.manifest.status).toBe("human_audited_gold");
    expect(gold.manifest.pairs.filter(({ goldProvenance }) => goldProvenance.humanFeedbackId !== null)).toHaveLength(32);
    expect(gold.manifest.pairs[0].goldProvenance.source).toBe("human_correction");

    const incomplete = clone(humanFeedback);
    incomplete.decisions.pop();
    expect(() => promoteStageAGold(silver, silver.silverDigest, CALIBRATION_DIGEST, policy, policyDigest, incomplete)).toThrow(/complete_precommitted_audit_required/u);
    const unapproved = clone(humanFeedback) as Mutable<StageAHumanFeedbackV2>;
    unapproved.approved = false;
    expect(() => promoteStageAGold(silver, silver.silverDigest, CALIBRATION_DIGEST, policy, policyDigest, unapproved)).toThrow(/explicit_human_approval_required/u);
    const unclear = clone(humanFeedback);
    unclear.decisions[0].judgment = "unclear";
    expect(() => promoteStageAGold(silver, silver.silverDigest, CALIBRATION_DIGEST, policy, policyDigest, unclear)).toThrow(/resolved_human_feedback_required/u);
    const participant = clone(humanFeedback);
    participant.reviewerId = "eval-adjudicator";
    expect(() => promoteStageAGold(silver, silver.silverDigest, CALIBRATION_DIGEST, policy, policyDigest, participant)).toThrow(/independent_human_reviewer_required/u);
  });

  it("rejects audit policies above 32 and unknown precommitted pairs", () => {
    const silver = freezeStageASilver(syntheticWip(), CALIBRATION_DIGEST);
    const oversized = auditPolicy(silver.silverDigest, 32) as Mutable<StageAHumanAuditPolicyV2>;
    oversized.auditPairIds.push("eval-pair-032");
    expect(() => digestStageAHumanAuditPolicy(oversized)).toThrow(/bounded_array_required/u);
    const unknown = auditPolicy(silver.silverDigest);
    const mutable = clone(unknown);
    mutable.auditPairIds[0] = "eval-pair-999";
    const digest = digestStageAHumanAuditPolicy(mutable);
    expect(() => promoteStageAGold(silver, silver.silverDigest, CALIBRATION_DIGEST, mutable, digest, feedback(mutable, digest))).toThrow(/known_pair_ids_required/u);
  });
});

describe("gold-only loading and private files", () => {
  it("returns metadata-free target inputs and separate scoring labels only after gold", async () => {
    const { gold, silver, policyDigest } = await writeGoldFixture();
    const args = ["gold.json", gold.goldDigest, silver.silverDigest, policyDigest, CALIBRATION_DIGEST] as const;
    const targets = await loadStageATargetInputs(...args);
    const labels = await loadStageAScoringLabels(...args);
    expect(targets).toHaveLength(200);
    expect(Object.keys(targets[0])).toEqual(["pairId", "query", "classifierInput"]);
    expect(canonicalStageAJson(targets)).not.toMatch(/goldLabel|silverLabel|persona|cohort|actorId|modelVersion/u);
    expect(labels[0]).toEqual({ pairId: "eval-pair-000", label: "reject" });
    await expect(loadStageATargetInputs("gold.json", "0".repeat(64), silver.silverDigest, policyDigest, CALIBRATION_DIGEST)).rejects.toThrow(/gold_digest_mismatch/u);
  });

  it("reports production-shaped and challenge cohorts separately", async () => {
    const { gold, silver, policyDigest } = await writeGoldFixture();
    const report = await reportStageAGoldFreeze("gold.json", gold.goldDigest, silver.silverDigest, policyDigest, CALIBRATION_DIGEST);
    expect(report.cohorts.production_shaped.totalPairs).toBe(120);
    expect(report.cohorts.challenge.totalPairs).toBe(80);
    expect(report).not.toHaveProperty("totalPairs");
  });

  it("publishes silver once with private permissions and refuses overwrite", async () => {
    const root = await useTemporaryRoot();
    const result = await writeStageASilverFreezeFile("silver.json", syntheticWip(), CALIBRATION_DIGEST);
    expect(result.silverDigest).toMatch(/^[a-f0-9]{64}$/u);
    expect((await stat(path.join(root, "silver.json"))).mode & 0o077).toBe(0);
    await expect(writeStageASilverFreezeFile("silver.json", syntheticWip(), CALIBRATION_DIGEST)).rejects.toEqual(new StageAEvaluationError("$file", "exclusive_publish_failed"));
    await expect(writeStageASilverFreezeFile("../escape.json", syntheticWip(), CALIBRATION_DIGEST)).rejects.toThrow(/safe_file_name_required/u);
  });

  it("rejects duplicate JSON keys without leaking values or paths", async () => {
    const root = await useTemporaryRoot();
    await writeFile(path.join(root, "policy.json"), '{"schemaVersion":"ai-filter-stage-a-human-audit-policy-v2","sourceSilverDigest":"secret","sourceSilverDigest":"other","auditPairIds":[]}', { mode: 0o600 });
    let caught: unknown;
    try {
      await readStageAHumanAuditPolicyFile("policy.json");
    } catch (error) {
      caught = error;
    }
    expect(caught).toEqual(new StageAEvaluationError("$file", "duplicate_json_key"));
    expect(String(caught)).not.toContain("secret");
    expect(String(caught)).not.toContain(root);
  });

  it("rejects tampered gold despite a valid-looking envelope", async () => {
    const { root, gold, silver, policyDigest } = await writeGoldFixture();
    const tampered = clone(gold);
    tampered.manifest.pairs[40].goldLabel = tampered.manifest.pairs[40].goldLabel === "accept" ? "reject" : "accept";
    await writeFile(path.join(root, "tampered.json"), canonicalStageAJson(tampered), { mode: 0o600 });
    await expect(loadStageATargetInputs("tampered.json", gold.goldDigest, silver.silverDigest, policyDigest, CALIBRATION_DIGEST)).rejects.toThrow(/gold_provenance_mismatch|gold_digest_mismatch/u);
  });

  it("stores calibration only as an external digest", async () => {
    const { root, gold } = await writeGoldFixture();
    const bytes = await readFile(path.join(root, "gold.json"), "utf8");
    expect(bytes).toContain(`"calibrationDigest":"${CALIBRATION_DIGEST}"`);
    expect(bytes).not.toContain("calibrationExampleId");
    expect(gold.schemaVersion).toBe(STAGE_A_GOLD_FREEZE_SCHEMA_VERSION);
  });
});
