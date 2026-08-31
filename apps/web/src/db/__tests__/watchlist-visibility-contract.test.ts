import { describe, expect, it } from "vitest";

import {
  assertWatchlistPathVariantMatrix,
  expectedWatchlistPathVariants,
  requiredWatchlistApplyEvidence,
} from "../../../scripts/watchlist-visibility-contract";

const validApplyEnvironment = {
  WATCHLIST_PRIVACY_CONFIRMATION: "PRIVATE-WATCHLISTS-0089",
  WATCHLIST_PRIVACY_BACKUP_RESTORE_RUN_ID: "101",
  WATCHLIST_PRIVATE_MUTATIONS_DEPLOY_SHA: "1".repeat(40),
  WATCHLIST_ROUTE_CUTOVER_DEPLOY_SHA: "2".repeat(40),
  WATCHLIST_ROUTE_CUTOVER_APPROVED_BY: "privacy-reviewer",
  WATCHLIST_PUBLIC_API_CUTOVER_DEPLOY_SHA: "3".repeat(40),
  WATCHLIST_PUBLIC_API_CUTOVER_VERIFICATION_RUN_ID: "202",
} as const;

describe("watchlist visibility path inventory", () => {
  it("retains both labels and four distinct URL forms when usernames match", () => {
    const params = {
      watchlistId: "watchlist-equal",
      ownerUsername: "alice",
      ownerDisplayUsername: "alice",
      watchlistSlug: "engineering",
    };
    const variants = expectedWatchlistPathVariants(params);

    expect(variants).toHaveLength(8);
    expect(new Set(variants.map((variant) => variant.pagePath)).size).toBe(4);
    expect(new Set(variants.map((variant) => variant.ogPath)).size).toBe(4);
    expect(
      new Set(variants.map((variant) => variant.legacyOgPathPattern)),
    ).toHaveProperty("size", 4);
    expect(
      new Set(variants.map((variant) => variant.legacyOgPurgePattern)),
    ).toHaveProperty("size", 4);
    expect(new Set(variants.map((variant) => variant.ownerSlugKind))).toEqual(
      new Set(["username", "display_username"]),
    );
    expect(() => assertWatchlistPathVariantMatrix(params, variants)).not.toThrow();
  });

  it("retains eight distinct URL forms when username values differ", () => {
    const params = {
      watchlistId: "watchlist-different",
      ownerUsername: "alice-canonical",
      ownerDisplayUsername: "alice-display",
      watchlistSlug: "engineering",
    };
    const variants = expectedWatchlistPathVariants(params);

    expect(variants).toHaveLength(8);
    for (const field of [
      "pagePath",
      "ogPath",
      "legacyOgPathPattern",
      "legacyOgPurgePattern",
    ] as const) {
      expect(new Set(variants.map((variant) => variant[field])).size).toBe(8);
    }
    expect(() => assertWatchlistPathVariantMatrix(params, variants)).not.toThrow();
  });

  it("rejects a missing labelled URL variant", () => {
    const params = {
      watchlistId: "watchlist-incomplete",
      ownerUsername: "alice",
      ownerDisplayUsername: "alice",
      watchlistSlug: "engineering",
    };
    const variants = expectedWatchlistPathVariants(params);

    expect(() =>
      assertWatchlistPathVariantMatrix(params, variants.slice(1)),
    ).toThrow(/incomplete or duplicated/);
  });
});

describe("watchlist visibility apply evidence", () => {
  it("binds immutable #8367 deployment and verification evidence", () => {
    const evidence = requiredWatchlistApplyEvidence(validApplyEnvironment, {
      publicCount: 7,
      publicInventoryDigest: "a".repeat(32),
    });

    expect(evidence.publicApiCutoverDeploySha).toBe("3".repeat(40));
    expect(evidence.publicApiCutoverVerificationRunId).toBe(202);
    expect(evidence.expectedPublicCount).toBe(7);
    expect(evidence.expectedPublicDigest).toBe("a".repeat(32));
  });

  it.each([
    ["WATCHLIST_PUBLIC_API_CUTOVER_DEPLOY_SHA", /deployed 40-hex SHA/],
    [
      "WATCHLIST_PUBLIC_API_CUTOVER_VERIFICATION_RUN_ID",
      /positive immutable run ID/,
    ],
  ] as const)("fails closed without %s", (name, message) => {
    const environment: Record<string, string | undefined> = {
      ...validApplyEnvironment,
      [name]: undefined,
    };

    expect(() =>
      requiredWatchlistApplyEvidence(environment, {
        publicCount: 7,
        publicInventoryDigest: "a".repeat(32),
      }),
    ).toThrow(message);
  });
});
