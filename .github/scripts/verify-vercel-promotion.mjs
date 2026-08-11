#!/usr/bin/env node

import { execFile } from "node:child_process";
import { appendFile } from "node:fs/promises";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";

const execFileAsync = promisify(execFile);
const SHA_RE = /^[0-9a-f]{40}$/;
const DEPLOYMENT_ID_RE = /^dpl_[A-Za-z0-9]+$/;
const ALIAS_RE = /^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$/;
const VERCEL_URL_RE = /^https:\/\/[A-Za-z0-9.-]+\.vercel\.app\/?$/;

export class PromotionVerificationError extends Error {
  constructor(message, observation) {
    super(message);
    this.name = "PromotionVerificationError";
    this.observation = observation;
  }
}

function requireMatch(value, pattern, name) {
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new TypeError(`Invalid ${name}`);
  }
  return value;
}

function requirePositiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new TypeError(`Invalid ${name}`);
  }
  return value;
}

function safeValue(value) {
  if (typeof value !== "string") return "unavailable";
  return value.replace(/[^A-Za-z0-9._:/-]/g, "?").slice(0, 200) || "unavailable";
}

export function parseProductionDeployment(raw) {
  let deployment;
  try {
    deployment = JSON.parse(raw);
  } catch {
    throw new TypeError("Vercel returned malformed JSON");
  }
  if (!deployment || Array.isArray(deployment) || typeof deployment !== "object") {
    throw new TypeError("Vercel returned an invalid deployment object");
  }

  const sha = deployment.gitSource?.sha ?? deployment.meta?.githubCommitSha;
  const id = deployment.id;
  const rawUrl = deployment.url;
  const url = typeof rawUrl === "string" && rawUrl.startsWith("https://")
    ? rawUrl
    : `https://${rawUrl ?? ""}`;

  return {
    sha: requireMatch(sha, SHA_RE, "deployment SHA"),
    id: requireMatch(id, DEPLOYMENT_ID_RE, "deployment ID"),
    url: requireMatch(url, VERCEL_URL_RE, "deployment URL"),
  };
}

export async function verifyPromotion({
  expectedSha,
  expectedId,
  expectedUrl,
  alias,
  attempts = 12,
  delayMs = 5_000,
  fetchProduction,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  requireMatch(expectedSha, SHA_RE, "EXPECTED_SHA");
  requireMatch(expectedId, DEPLOYMENT_ID_RE, "EXPECTED_DEPLOYMENT_ID");
  requireMatch(expectedUrl, VERCEL_URL_RE, "EXPECTED_DEPLOYMENT_URL");
  requireMatch(alias, ALIAS_RE, "PRODUCTION_ALIAS");
  requirePositiveInteger(attempts, "attempts");
  if (!Number.isSafeInteger(delayMs) || delayMs < 0) {
    throw new TypeError("Invalid delayMs");
  }
  if (typeof fetchProduction !== "function") {
    throw new TypeError("fetchProduction is required");
  }

  let observation = { sha: "unavailable", id: "unavailable", url: "unavailable" };
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      observation = parseProductionDeployment(await fetchProduction());
      if (observation.sha === expectedSha && observation.id === expectedId) {
        return { ...observation, attempt };
      }
    } catch {
      observation = {
        sha: "unavailable",
        id: "unavailable",
        url: "error:Vercel_API_unavailable",
      };
    }
    if (attempt < attempts) await sleep(delayMs);
  }

  throw new PromotionVerificationError(
    `Production alias ${alias} did not resolve to the promoted deployment after ${attempts} attempts`,
    observation,
  );
}

async function writeFailureSummary(error, expected) {
  if (!process.env.GITHUB_STEP_SUMMARY) return;
  const observed = error instanceof PromotionVerificationError
    ? error.observation
    : { sha: "unavailable", id: "unavailable", url: "unavailable" };
  await appendFile(
    process.env.GITHUB_STEP_SUMMARY,
    [
      "## Vercel promotion identity mismatch",
      "",
      `- Expected SHA: \`${safeValue(expected.sha)}\``,
      `- Expected deployment: \`${safeValue(expected.id)}\` (${safeValue(expected.url)})`,
      `- Observed SHA: \`${safeValue(observed.sha)}\``,
      `- Observed deployment: \`${safeValue(observed.id)}\` (${safeValue(observed.url)})`,
      "",
      "The production alias did not converge to the deployment that this workflow promoted.",
      "",
    ].join("\n"),
  );
}

async function main() {
  const expected = {
    sha: process.env.EXPECTED_SHA,
    id: process.env.EXPECTED_DEPLOYMENT_ID,
    url: process.env.EXPECTED_DEPLOYMENT_URL,
  };
  const alias = process.env.PRODUCTION_ALIAS;
  const orgId = process.env.VERCEL_ORG_ID;
  const token = process.env.VERCEL_TOKEN;
  requireMatch(orgId, /^[A-Za-z0-9_-]+$/, "VERCEL_ORG_ID");
  if (typeof token !== "string" || token.length === 0) {
    throw new TypeError("Invalid VERCEL_TOKEN");
  }

  try {
    const result = await verifyPromotion({
      expectedSha: expected.sha,
      expectedId: expected.id,
      expectedUrl: expected.url,
      alias,
      fetchProduction: async () => {
        const { stdout } = await execFileAsync(
          "pnpm",
          [
            "dlx",
            "vercel@55.0.0",
            "api",
            `/v13/deployments/${alias}?teamId=${orgId}`,
            "--raw",
            `--token=${token}`,
          ],
          { encoding: "utf8", maxBuffer: 1_000_000 },
        );
        return stdout;
      },
    });
    process.stdout.write(`${JSON.stringify({
      event: "vercel_promotion_verified",
      expectedSha: expected.sha,
      deploymentId: expected.id,
      deploymentUrl: expected.url,
      productionUrl: result.url,
      attempt: result.attempt,
    })}\n`);
  } catch (error) {
    await writeFailureSummary(error, expected);
    const observed = error instanceof PromotionVerificationError
      ? error.observation
      : { sha: "unavailable", id: "unavailable", url: "unavailable" };
    const message = error instanceof Error ? error.message : String(error);
    process.stdout.write(
      `::error title=Vercel promotion identity mismatch::${safeValue(message)}. ` +
      `Expected ${safeValue(expected.sha)} / ${safeValue(expected.id)} at ${safeValue(expected.url)}; ` +
      `observed ${safeValue(observed.sha)} / ${safeValue(observed.id)} at ${safeValue(observed.url)}\n`,
    );
    process.exitCode = 1;
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    process.stderr.write(`${safeValue(error instanceof Error ? error.message : String(error))}\n`);
    process.exitCode = 2;
  });
}
