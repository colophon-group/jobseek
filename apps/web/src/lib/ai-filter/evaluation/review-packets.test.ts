import { describe, expect, it } from "vitest";
import {
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
  normalizeClassifierInputV1,
} from "../classifier-input";
import { AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION } from "../contract";
import {
  STAGE_A_BUNDLE_SCHEMA_VERSION,
  STAGE_A_FILTER_SCHEMA_VERSION,
  STAGE_A_PAIR_SCHEMA_VERSION,
  STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION,
  STAGE_A_WIP_SCHEMA_VERSION,
  digestStageAHumanAuditPolicy,
  freezeStageASilver,
  type StageAWipV2,
} from "./stage-a";
import {
  digestStageACalibrationArtifact,
  renderStageACalibrationPacket,
  renderStageALabelAuditPacket,
  renderStageAPromptReviewPacket,
  type StageACalibrationArtifact,
} from "./review-packets";

const CALIBRATION_DIGEST = "a".repeat(64);

function syntheticWip(): StageAWipV2 {
  const filters = Array.from({ length: 20 }, (_, index) => ({
    schemaVersion: STAGE_A_FILTER_SCHEMA_VERSION,
    filterId: `eval-filter-${String(index).padStart(2, "0")}`,
    source: "production_deidentified" as const,
    sourceFilterDigest: index.toString(16).padStart(64, "0"),
    generalizedContext: {
      companyScope: "selected" as const,
      locationScope: "single" as const,
      occupationScope: "multiple" as const,
      keywordScope: "none" as const,
      seniorityScope: "single" as const,
      technologyScope: "multiple" as const,
      workModeScope: "single" as const,
      employmentTypeScope: "none" as const,
      compensationScope: "minimum" as const,
      experienceScope: "range" as const,
      locale: "en" as const,
    },
  }));
  const bundles = Array.from({ length: 25 }, (_, index) => ({
    schemaVersion: STAGE_A_BUNDLE_SCHEMA_VERSION,
    bundleId: `eval-bundle-${String(index).padStart(2, "0")}`,
    filterId: `eval-filter-${String(index % 20).padStart(2, "0")}`,
    cohort: index < 15 ? "production_shaped" as const : "challenge" as const,
    persona: index % 2 === 0 ? "lazy" as const : "verbose" as const,
    softQuery: index === 0 ? "role with ``` and <script> text" : `synthetic prompt ${index}`,
    promptProvenance: {
      origin: "agent_synthetic" as const,
      authorId: "eval-author-secret",
      agentRole: "role-secret",
      model: "model-secret",
      modelVersion: "version-secret",
      reasoningEffort: "high" as const,
      taskPromptDigest: (index + 100).toString(16).padStart(64, "0"),
    },
  }));
  const pairs = bundles.flatMap((bundle, bundleIndex) => Array.from({ length: 8 }, (_, offset) => {
    const index = bundleIndex * 8 + offset;
    const classifierSource = {
      candidateId: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
      title: index === 0 ? "Role ``` \u202e<script>alert</script>" : `Role ${index}`,
      companyName: "Synthetic Company",
      descriptionHtml: `<p>Description ${index}\r\nwith evidence</p>`,
      selectedDescriptionLocale: "en",
    };
    return {
      schemaVersion: STAGE_A_PAIR_SCHEMA_VERSION,
      pairId: `eval-pair-${String(index).padStart(3, "0")}`,
      bundleId: bundle.bundleId,
      locale: "en" as const,
      evidenceCondition: "direct_support" as const,
      ambiguity: false,
      classifierSource,
      contentIdentity: normalizeClassifierInputV1(classifierSource).contentIdentity,
      annotations: [
        { annotationId: `eval-work-${String(index).padStart(3, "0")}-a`, actorId: "eval-annotator-secret-a", label: "accept" as const },
        { annotationId: `eval-work-${String(index).padStart(3, "0")}-b`, actorId: "eval-annotator-secret-b", label: "accept" as const },
      ] as const,
      adjudication: null,
    };
  }));
  return {
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: "eval-review-packet-data",
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

function calibration(count = 24): StageACalibrationArtifact {
  return {
    schemaVersion: "ai-filter-stage-a-calibration-v2",
    calibrationId: "eval-calibration-detached",
    examples: Array.from({ length: count }, (_, index) => {
      const classifierSource = {
        candidateId: `10000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
        title: index === 0 ? "Calibration ``` role" : `Calibration role ${index}`,
        companyName: "Synthetic Company",
        descriptionHtml: `<p>Detached synthetic posting ${index}</p>`,
        selectedDescriptionLocale: "en",
      };
      return {
        calibrationExampleId: `eval-calibration-example-${String(index).padStart(2, "0")}`,
        softQuery: `synthetic calibration prompt ${index}`,
        classifierSource,
        contentIdentity: normalizeClassifierInputV1(classifierSource).contentIdentity,
      };
    }),
  };
}

describe("static Stage A review packets", () => {
  it("renders exactly 12 deterministic prompt cards without private or agent metadata", () => {
    const wip = syntheticWip();
    const ids = wip.bundles.slice(0, 12).map(({ bundleId }) => bundleId).reverse();
    const first = renderStageAPromptReviewPacket(wip, ids);
    const second = renderStageAPromptReviewPacket(wip, [...ids].reverse());
    expect(first).toBe(second);
    expect(first.match(/^## /gmu)).toHaveLength(12);
    expect(first.match(/Decision: \[ \] keep/gmu)).toHaveLength(12);
    expect(first).toContain("Generalized filter context:");
    expect(first).toContain("````text\nrole with ``` and <script> text\n````");
    expect(first).toContain("\\u202e");
    expect(first).not.toContain("\u202e");
    expect(first).not.toMatch(/model-secret|version-secret|eval-author-secret|sourceFilterDigest|00000000-/u);
    expect(first).not.toContain("\r");
  });

  it("rejects prompt review selections other than exactly 12 unique known IDs", () => {
    const wip = syntheticWip();
    const ids = wip.bundles.slice(0, 12).map(({ bundleId }) => bundleId);
    expect(() => renderStageAPromptReviewPacket(wip, ids.slice(0, 11))).toThrow(/exact_card_count_required/u);
    expect(() => renderStageAPromptReviewPacket(wip, [...ids.slice(0, 11), ids[0]])).toThrow(/unique_ids_required/u);
    expect(() => renderStageAPromptReviewPacket(wip, [...ids.slice(0, 11), "eval-bundle-99"])).toThrow(/known_ids_required/u);
  });

  it("renders a bounded blind label audit with no agent decisions or provenance", () => {
    const silver = freezeStageASilver(syntheticWip(), CALIBRATION_DIGEST);
    const ids = silver.manifest.pairs.slice(0, 32).map(({ pairId }) => pairId).reverse();
    const policy = { schemaVersion: STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION, sourceSilverDigest: silver.silverDigest, auditPairIds: ids };
    const policyDigest = digestStageAHumanAuditPolicy(policy);
    const markdown = renderStageALabelAuditPacket(silver, silver.silverDigest, CALIBRATION_DIGEST, policy, policyDigest);
    expect(markdown.match(/^## /gmu)).toHaveLength(32);
    expect(markdown.match(/Judgment: \[ \] accept/gmu)).toHaveLength(32);
    expect(markdown).toContain("Cohort: production_shaped");
    expect(markdown).not.toMatch(/silverLabel|silverProvenance|annotationId|actorId|eval-annotator-secret|model-secret/u);
    expect(markdown).not.toMatch(/00000000-/u);
  });

  it("rejects empty, oversized, duplicate and tampered label-audit selections", () => {
    const silver = freezeStageASilver(syntheticWip(), CALIBRATION_DIGEST);
    const ids = silver.manifest.pairs.slice(0, 33).map(({ pairId }) => pairId);
    const policyFor = (auditPairIds: string[]) => ({ schemaVersion: STAGE_A_HUMAN_AUDIT_POLICY_SCHEMA_VERSION, sourceSilverDigest: silver.silverDigest, auditPairIds });
    expect(() => renderStageALabelAuditPacket(silver, silver.silverDigest, CALIBRATION_DIGEST, policyFor([]), "0".repeat(64))).toThrow(/bounded_array_required/u);
    expect(() => renderStageALabelAuditPacket(silver, silver.silverDigest, CALIBRATION_DIGEST, policyFor(ids), "0".repeat(64))).toThrow(/bounded_array_required/u);
    const validPolicy = policyFor(ids.slice(0, 8));
    const validDigest = digestStageAHumanAuditPolicy(validPolicy);
    expect(() => renderStageALabelAuditPacket(silver, "0".repeat(64), CALIBRATION_DIGEST, validPolicy, validDigest)).toThrow(/silver_digest_mismatch/u);
    expect(() => renderStageALabelAuditPacket(silver, silver.silverDigest, CALIBRATION_DIGEST, validPolicy, "0".repeat(64))).toThrow(/audit_policy_pin_mismatch/u);
  });

  it("renders a detached deterministic 24–32 example calibration packet", () => {
    const artifact = calibration(24);
    const markdown = renderStageACalibrationPacket(artifact);
    expect(markdown.match(/^## /gmu)).toHaveLength(24);
    expect(markdown).toContain("Stage A calibration review: eval-calibration-detached");
    expect(markdown).toContain("Synthetic prompt:");
    expect(markdown).not.toMatch(/model|reasoningEffort|prediction|output/u);
    expect(digestStageACalibrationArtifact(artifact)).toMatch(/^[a-f0-9]{64}$/u);
    expect(digestStageACalibrationArtifact(artifact)).toBe(digestStageACalibrationArtifact(calibration(24)));
    expect(renderStageACalibrationPacket(calibration(32)).match(/^## /gmu)).toHaveLength(32);
  });

  it("rejects calibration outside 24–32 and any model/output metadata", () => {
    expect(() => renderStageACalibrationPacket(calibration(23))).toThrow(/bounded_array_required/u);
    expect(() => renderStageACalibrationPacket(calibration(33))).toThrow(/bounded_array_required/u);
    const polluted = calibration(24) as StageACalibrationArtifact & Record<string, unknown>;
    (polluted as Record<string, unknown>).model = "secret-model";
    expect(() => renderStageACalibrationPacket(polluted)).toThrow(/additional_properties/u);
  });
});
