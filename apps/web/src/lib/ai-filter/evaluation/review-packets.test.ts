import { describe, expect, it } from "vitest";
import { normalizeClassifierInputV1 } from "../classifier-input";
import {
  digestStageACalibrationArtifact,
  renderStageACalibrationPacket,
  type StageACalibrationArtifact,
} from "./review-packets";

function calibration(count = 24): StageACalibrationArtifact {
  return {
    schemaVersion: "ai-filter-stage-a-calibration-v2",
    calibrationId: "eval-calibration-detached",
    examples: Array.from({ length: count }, (_, index) => {
      const classifierSource = {
        candidateId: `10000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
        title: index === 0 ? "Calibration ``` role \u202e<script>" : `Calibration role ${index}`,
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

describe("static Stage A calibration packet", () => {
  it("renders a detached deterministic 24–32 item packet without agent metadata", () => {
    const artifact = calibration();
    const markdown = renderStageACalibrationPacket(artifact);
    expect(markdown.match(/^## /gmu)).toHaveLength(24);
    expect(markdown).toContain("Stage A calibration review: eval-calibration-detached");
    expect(markdown).toContain("\\u202e");
    expect(markdown).not.toContain("\u202e");
    expect(markdown).not.toMatch(/modelVersion|reasoningEffort|prediction/u);
    expect(digestStageACalibrationArtifact(artifact)).toBe(digestStageACalibrationArtifact(calibration()));
    expect(renderStageACalibrationPacket(calibration(32)).match(/^## /gmu)).toHaveLength(32);
  });

  it("rejects out-of-range calibration and extra metadata", () => {
    expect(() => renderStageACalibrationPacket(calibration(23))).toThrow(/bounded_array_required/u);
    expect(() => renderStageACalibrationPacket(calibration(33))).toThrow(/bounded_array_required/u);
    const polluted = calibration() as StageACalibrationArtifact & Record<string, unknown>;
    (polluted as Record<string, unknown>).model = "secret-model";
    expect(() => renderStageACalibrationPacket(polluted)).toThrow(/additional_properties/u);
  });
});
