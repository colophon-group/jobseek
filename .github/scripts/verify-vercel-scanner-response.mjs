#!/usr/bin/env node

import { readFileSync, statSync } from "node:fs";
import { pathToFileURL } from "node:url";

const HTTP_STATUS_RE = /^HTTP\/(?:\d+(?:\.\d+)?)\s+([1-5]\d{2})(?:\s|$)/;
const HEADER_NAME_RE = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;
const MAX_HEADER_BYTES = 1_000_000;

export class ScannerResponseError extends Error {
  constructor(message) {
    super(message);
    this.name = "ScannerResponseError";
  }
}

function parseHeaderBlocks(rawHeaders) {
  if (typeof rawHeaders !== "string" || rawHeaders.length === 0) {
    throw new ScannerResponseError("Scanner response headers are missing");
  }

  const blocks = [];
  let current = null;

  for (const line of rawHeaders.split(/\r?\n/)) {
    const statusMatch = HTTP_STATUS_RE.exec(line);
    if (statusMatch) {
      if (current) blocks.push(current);
      current = { status: statusMatch[1], mitigationValues: [], malformed: false };
      continue;
    }

    if (!current || line === "") continue;
    if (/^[ \t]/.test(line)) {
      current.malformed = true;
      continue;
    }

    const colon = line.indexOf(":");
    if (colon <= 0) {
      current.malformed = true;
      continue;
    }

    const name = line.slice(0, colon);
    if (!HEADER_NAME_RE.test(name)) {
      current.malformed = true;
      continue;
    }
    if (name.toLowerCase() === "x-vercel-mitigated") {
      current.mitigationValues.push(line.slice(colon + 1).trim());
    }
  }

  if (current) blocks.push(current);
  if (blocks.length === 0) {
    throw new ScannerResponseError("Scanner response has no HTTP header block");
  }
  return blocks;
}

export function verifyScannerResponse(status, rawHeaders) {
  if (typeof status !== "string" || !/^[1-5]\d{2}$/.test(status)) {
    throw new ScannerResponseError("Scanner response has an invalid HTTP status");
  }

  const blocks = parseHeaderBlocks(rawHeaders);
  const finalBlock = blocks.at(-1);
  if (finalBlock.status !== status) {
    throw new ScannerResponseError(
      `Scanner final header status ${finalBlock.status} does not match curl status ${status}`,
    );
  }

  if (status === "404") {
    return { status, outcome: "not_found" };
  }

  if (status === "403") {
    if (finalBlock.malformed) {
      throw new ScannerResponseError("Scanner 403 has malformed final headers");
    }
    if (
      finalBlock.mitigationValues.length === 1 &&
      finalBlock.mitigationValues[0] === "deny"
    ) {
      return { status, outcome: "vercel_mitigated" };
    }
    throw new ScannerResponseError(
      "Scanner 403 lacks one exact x-vercel-mitigated: deny header",
    );
  }

  throw new ScannerResponseError(`Scanner path returned disallowed HTTP ${status}`);
}

function main() {
  const [status, headerPath, ...extra] = process.argv.slice(2);
  if (!status || !headerPath || extra.length > 0) {
    throw new ScannerResponseError(
      "usage: verify-vercel-scanner-response.mjs <status> <header-file>",
    );
  }
  if (statSync(headerPath).size > MAX_HEADER_BYTES) {
    throw new ScannerResponseError("Scanner response headers exceed the size limit");
  }

  const result = verifyScannerResponse(status, readFileSync(headerPath, "utf8"));
  const description = result.outcome === "not_found"
    ? "not found"
    : "Vercel mitigation confirmed";
  process.stdout.write(`Scanner path accepted: HTTP ${result.status} (${description})\n`);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
