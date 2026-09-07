import { describe, expect, it } from "vitest";
import { siteConfig } from "@/content/config";

describe("homepage feature icon contract", () => {
  it("names every configured icon after the feature concept it represents", () => {
    expect(
      siteConfig.features.sections.map(({ pointIcons }) => [...pointIcons]),
    ).toEqual([
      ["source", "filters", "saved"],
      ["tracking", "interviews", "stats"],
      ["curate", "companies", "organize"],
    ]);
  });
});
