#!/usr/bin/env node

import { readFileSync, statSync } from "node:fs";
import { pathToFileURL } from "node:url";

const MAX_BODY_BYTES = 10_000_000;
const SAFE_ROUTE_RE = /^\/[\x21-\x7e]*$/;
const NEXT_STREAM_ERROR_RE = /\$RX\s*\(/;
const BETTER_AUTH_SCHEMA_ERROR_RE = /(?:SCHEMA_MISMATCH|Drizzle schema mismatch)/i;

export class SmokeResponseError extends Error {
  constructor(message) {
    super(message);
    this.name = "SmokeResponseError";
  }
}

export function verifySmokeResponse(route, status, body) {
  if (typeof route !== "string" || !SAFE_ROUTE_RE.test(route)) {
    throw new SmokeResponseError("Smoke route is invalid");
  }
  if (typeof status !== "string" || !/^[1-5]\d{2}$/.test(status)) {
    throw new SmokeResponseError(`${route} returned an invalid HTTP status`);
  }
  if (status !== "200") {
    throw new SmokeResponseError(`${route} returned HTTP ${status}`);
  }
  if (typeof body !== "string" || body.length === 0) {
    throw new SmokeResponseError(`${route} returned an empty response body`);
  }
  if (BETTER_AUTH_SCHEMA_ERROR_RE.test(body)) {
    throw new SmokeResponseError(`${route} contains a Better Auth schema error`);
  }
  if (NEXT_STREAM_ERROR_RE.test(body)) {
    throw new SmokeResponseError(`${route} contains a streamed server error`);
  }
  if (route === "/en/sign-in" && !body.includes("Sign in")) {
    throw new SmokeResponseError(`${route} does not contain the sign-in form`);
  }

  return { route, status };
}

function main() {
  const [route, status, bodyPath, ...extra] = process.argv.slice(2);
  if (!route || !status || !bodyPath || extra.length > 0) {
    throw new SmokeResponseError(
      "usage: verify-vercel-smoke-response.mjs <route> <status> <body-file>",
    );
  }
  if (statSync(bodyPath).size > MAX_BODY_BYTES) {
    throw new SmokeResponseError(`${route} response body exceeds the size limit`);
  }

  const result = verifySmokeResponse(route, status, readFileSync(bodyPath, "utf8"));
  process.stdout.write(`Smoke accepted: ${result.route} -> HTTP ${result.status}\n`);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : "Smoke verification failed");
    process.exitCode = 1;
  }
}
