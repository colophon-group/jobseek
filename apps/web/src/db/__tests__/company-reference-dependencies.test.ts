// @vitest-environment node
import { describe, expect, it } from "vitest";
import { assertCompanyDependencyLifecycle, companyDependencyManifest } from "../../../scripts/company-reference-dependency-check";

describe("company reference lifecycle declarations", () => {
  it.each(["bridge", "reference"] as const)("declares every owned replacement/disposition in %s", phase => {
    expect(() => assertCompanyDependencyLifecycle(companyDependencyManifest, phase)).not.toThrow();
  });
  it("refuses retiring the compatibility producer while selections still depend on legacy rows", () => {
    const manifest = structuredClone(companyDependencyManifest);
    manifest.retiredProducers.bridge.push("legacy_company_compatibility");
    expect(() => assertCompanyDependencyLifecycle(manifest, "bridge")).toThrow("without restrictive replacement lifecycle");
  });
  it("requires owned lifecycle evidence for retained history", () => {
    const manifest = structuredClone(companyDependencyManifest);
    manifest.foreignKeys.find(row => row.table === "company_request")!.disposition = "";
    expect(() => assertCompanyDependencyLifecycle(manifest, "reference")).toThrow("owner/disposition absent");
  });
  it("refuses retiring the replacement materializer or independent snapshot writer", () => {
    const manifest = structuredClone(companyDependencyManifest);
    manifest.retiredProducers.reference.push("web_reference_materializer");
    expect(() => assertCompanyDependencyLifecycle(manifest, "reference")).toThrow("without restrictive replacement lifecycle");
    manifest.retiredProducers.reference = ["saved_job_snapshot_writer"];
    expect(() => assertCompanyDependencyLifecycle(manifest, "reference")).toThrow("snapshot lifecycle absent");
  });
});
