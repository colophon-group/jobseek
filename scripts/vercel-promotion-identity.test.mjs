import assert from "node:assert/strict";
import test from "node:test";
import {
  PromotionVerificationError,
  parseProductionDeployment,
  verifyPromotion,
} from "../.github/scripts/verify-vercel-promotion.mjs";

const expectedSha = "a".repeat(40);
const staleSha = "b".repeat(40);
const expectedId = "dpl_Expected123";
const staleId = "dpl_Stale456";
const expectedUrl = "https://jobseek-expected.vercel.app";

function deployment({
  sha = expectedSha,
  id = expectedId,
  url = "jobseek-expected.vercel.app",
} = {}) {
  return JSON.stringify({ gitSource: { sha }, id, url });
}

function options(fetchProduction) {
  return {
    expectedSha,
    expectedId,
    expectedUrl,
    alias: "jseek.co",
    fetchProduction,
    delayMs: 0,
    sleep: async () => {},
  };
}

test("accepts only the exact promoted deployment identity", async () => {
  let calls = 0;
  const result = await verifyPromotion(options(async () => {
    calls += 1;
    return deployment();
  }));

  assert.deepEqual(result, {
    sha: expectedSha,
    id: expectedId,
    url: expectedUrl,
    attempt: 1,
  });
  assert.equal(calls, 1);
});

test("polls through stale alias propagation before accepting exact identity", async () => {
  const responses = [
    deployment({ sha: staleSha, id: staleId, url: "jobseek-stale.vercel.app" }),
    deployment(),
  ];
  const result = await verifyPromotion({
    ...options(async () => responses.shift()),
    attempts: 2,
  });

  assert.equal(result.attempt, 2);
  assert.equal(responses.length, 0);
});

test("fails after a bounded number of permanent mismatches", async () => {
  let calls = 0;
  await assert.rejects(
    verifyPromotion({
      ...options(async () => {
        calls += 1;
        return deployment({ sha: staleSha, id: staleId });
      }),
      attempts: 3,
    }),
    (error) => {
      assert.ok(error instanceof PromotionVerificationError);
      assert.match(error.message, /after 3 attempts/);
      assert.deepEqual(error.observation, {
        sha: staleSha,
        id: staleId,
        url: expectedUrl,
      });
      return true;
    },
  );
  assert.equal(calls, 3);
});

test("reports malformed API responses without preserving control syntax", async () => {
  await assert.rejects(
    verifyPromotion({
      ...options(async () => "not-json\n::error secret"),
      attempts: 1,
    }),
    (error) => {
      assert.ok(error instanceof PromotionVerificationError);
      assert.equal(error.observation.sha, "unavailable");
      assert.doesNotMatch(error.observation.url, /\s|::/);
      assert.match(error.observation.url, /^error:Vercel/);
      return true;
    },
  );
});

test("does not expose Vercel command failures", async () => {
  await assert.rejects(
    verifyPromotion({
      ...options(async () => {
        throw new Error("command failed with --token=do-not-print");
      }),
      attempts: 1,
    }),
    (error) => {
      assert.ok(error instanceof PromotionVerificationError);
      assert.equal(error.observation.url, "error:Vercel_API_unavailable");
      assert.doesNotMatch(JSON.stringify(error), /do-not-print/);
      return true;
    },
  );
});

test("parses the legacy metadata SHA and an absolute deployment URL", () => {
  assert.deepEqual(parseProductionDeployment(JSON.stringify({
    meta: { githubCommitSha: expectedSha },
    id: expectedId,
    url: expectedUrl,
  })), {
    sha: expectedSha,
    id: expectedId,
    url: expectedUrl,
  });
});
