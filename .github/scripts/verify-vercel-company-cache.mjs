#!/usr/bin/env node

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";
import { verifySmokeResponse } from "./verify-vercel-smoke-response.mjs";

const execFileAsync = promisify(execFile);
const CLI = "vercel@59.25.4";
const ROUTES = ["/en/company/aircall", "/fr/company/hellofresh"];

export function verifyStaticCompanyTrace(trace, route, deploymentId) {
  const spans = trace?.spans;
  if (!Array.isArray(spans) || spans.length === 0) {
    throw new Error("Trace has no spans");
  }
  const root = spans.find((span) => span.name === `GET ${route}`);
  if (!root || root.attributes?.["vercel.deployment_id"] !== deploymentId) {
    throw new Error("Trace does not identify the staged route and deployment");
  }
  if (root.attributes?.["http.response.header.x-vercel-cache"] !== "HIT") {
    throw new Error("Company response is not yet a cache HIT");
  }
  if (spans.some((span) =>
    /Invoke Function|Dynamic PPR Content/.test(span.name ?? "") ||
    span.status?.code === 2
  )) {
    throw new Error("Cached company response still executes dynamic work or errors");
  }
  for (const name of ["Get Static PPR Content", "Stream Static PPR Content"]) {
    if (!spans.some((span) => span.name === name)) {
      throw new Error("Trace does not confirm complete static PPR delivery");
    }
  }
  return { route, deploymentId, cache: "HIT", functionInvocations: 0 };
}

async function main() {
  const { DEPLOYMENT_URL, EXPECTED_DEPLOYMENT_ID, VERCEL_ORG_ID } = process.env;
  if (!/^https:\/\/[A-Za-z0-9.-]+\.vercel\.app$/.test(DEPLOYMENT_URL ?? "") ||
      !/^dpl_[A-Za-z0-9]+$/.test(EXPECTED_DEPLOYMENT_ID ?? "") ||
      !/^team_[A-Za-z0-9]+$/.test(VERCEL_ORG_ID ?? "")) {
    throw new Error("Invalid staged deployment identity");
  }
  const run = async (args) => {
    const { stdout } = await execFileAsync("pnpm", ["dlx", CLI, ...args], {
      maxBuffer: 10_000_000,
      timeout: 60_000,
    });
    return JSON.parse(stdout);
  };
  for (const route of ROUTES) {
    let accepted = false;
    let reason = "Trace retrieval unavailable";
    for (let attempt = 1; attempt <= 6; attempt++) {
      let response;
      let trace;
      try {
        response = await run([
          "curl", route, "--deployment", DEPLOYMENT_URL,
          "--scope", VERCEL_ORG_ID, "--trace", "--json", "--yes", "--",
          "--silent", "--show-error", "--fail",
          "--user-agent", "Jobseek-Deployment-Cache-Check/1.0",
        ]);
        if (typeof response.requestId !== "string" || !response.requestId) {
          throw new Error("Missing request ID");
        }
        trace = await run([
          "traces", "get", response.requestId, "--json",
          "--scope", VERCEL_ORG_ID,
        ]);
      } catch {
        reason = "Trace retrieval unavailable";
      }
      if (trace) {
        try {
          verifySmokeResponse(route, "200", response.response);
          const result = verifyStaticCompanyTrace(trace, route, EXPECTED_DEPLOYMENT_ID);
          console.log(JSON.stringify({ ...result, attempt }));
          accepted = true;
          break;
        } catch (error) {
          reason = error.message;
        }
      }
      if (attempt < 6) await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    if (!accepted) throw new Error(`${route}: ${reason}`);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
