import { describe, expect, it } from "vitest";

import { normalizeClassifierInputV1 } from "./classifier-input";
import {
  aiFilterCacheKeysEqual,
  buildAiFilterCacheIdentity,
  serializeAiFilterCacheSemanticInput,
} from "./cache-key";

const secret = "test-only-cache-hmac-secret-with-32-bytes";

function job(descriptionHtml = "<p>Build reliable systems.</p>") {
  return normalizeClassifierInputV1({
    candidateId: "11111111-1111-4111-8111-111111111111",
    title: "Platform Engineer",
    companyName: "Acme",
    descriptionHtml,
    selectedDescriptionLocale: "en",
  });
}

describe("AI filter global exact cache identity", () => {
  it("canonicalizes equivalent queries to the same cross-user key", () => {
    const left = buildAiFilterCacheIdentity({
      queryText: "  remote\u00a0Rust  ",
      classifierInput: job(),
      hmacSecret: secret,
    });
    const right = buildAiFilterCacheIdentity({
      queryText: "remote Rust",
      classifierInput: job(),
      hmacSecret: secret,
    });
    expect(left.cacheKey).toBe(right.cacheKey);
    expect(aiFilterCacheKeysEqual(left.cacheKey, right.cacheKey)).toBe(true);
    expect(left.cacheKey).not.toContain("remote");
  });

  it("does not degrade when a hard-filter scope revision sees the same job", () => {
    const beforeScopeEdit = buildAiFilterCacheIdentity({
      queryText: "remote Rust",
      classifierInput: job(),
      hmacSecret: secret,
    });
    const afterScopeEdit = buildAiFilterCacheIdentity({
      queryText: "remote Rust",
      classifierInput: job(),
      hmacSecret: secret,
    });

    expect(afterScopeEdit.cacheKey).toBe(beforeScopeEdit.cacheKey);
  });

  it("misses when normalized content changes", () => {
    const left = buildAiFilterCacheIdentity({
      queryText: "remote Rust",
      classifierInput: job(),
      hmacSecret: secret,
    });
    const right = buildAiFilterCacheIdentity({
      queryText: "remote Rust",
      classifierInput: job("<p>Build reliable Go systems.</p>"),
      hmacSecret: secret,
    });
    expect(left.cacheKey).not.toBe(right.cacheKey);
  });

  it("binds the exact normalized model payload and all policy versions", () => {
    expect(JSON.parse(serializeAiFilterCacheSemanticInput({
      queryText: "remote Rust",
      classifierInput: job(),
    }))).toMatchObject({
      keyVersion: "ai-filter-cache-hmac-v1",
      model: "jev-1.13.0",
      promptVersion: "jev-job-fit-choice-v1",
      schemaVersion: "classifier-input-v1",
      normalizerVersion: "classifier-input-normalizer-v4",
      query: "remote Rust",
      job: {
        candidateId: "11111111-1111-4111-8111-111111111111",
        title: "Platform Engineer",
      },
    });
  });

  it("rejects short HMAC secrets", () => {
    expect(() => buildAiFilterCacheIdentity({
      queryText: "remote Rust",
      classifierInput: job(),
      hmacSecret: "too-short",
    })).toThrow("at least 32 bytes");
  });
});
