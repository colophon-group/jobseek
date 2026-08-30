import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

type OpenApiOperation = {
  responses?: Record<string, { $ref?: string }>;
};

type OpenApiDocument = {
  info: { description: string; version: string };
  paths: Record<string, { get?: OpenApiOperation }>;
  components: { responses: Record<string, { description: string }> };
};

const spec = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../public/api/openapi.json"),
    "utf8",
  ),
) as OpenApiDocument;

const PUBLIC_GET_PATHS = [
  "/api/v1/search",
  "/api/v1/job",
  "/api/v1/taxonomies",
  "/api/v1/companies",
  "/api/v1/watchlists",
  "/api/v1/watchlist/create",
  "/api/v1/resolve",
] as const;

describe("public API OpenAPI edge-rate-limit contract (#8261)", () => {
  it("documents the two rate-limit layers and cache-safe success headers", () => {
    expect(spec.info.version).toBe("1.2.0");
    expect(spec.info.description).toContain("60 requests per minute");
    expect(spec.info.description).toContain("30 requests per minute");
    expect(spec.info.description).toContain(
      "omit caller-specific rate-limit headers",
    );
  });

  it("documents the pre-cache 403 on every public GET operation", () => {
    for (const path of PUBLIC_GET_PATHS) {
      expect(spec.paths[path]?.get?.responses?.["403"]).toEqual({
        $ref: "#/components/responses/EdgeRateLimited",
      });
    }
    expect(spec.components.responses.EdgeRateLimited?.description).toContain(
      "generated before the application route",
    );
  });
});
