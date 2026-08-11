#!/usr/bin/env node

import { pathToFileURL } from "node:url";

export const SERVER_ACTION_KEY_NAME = "NEXT_SERVER_ACTIONS_ENCRYPTION_KEY";

export function verifyServerActionKey(value) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(
      `Production must define ${SERVER_ACTION_KEY_NAME} before building.`,
    );
  }

  const decoded = Buffer.from(value, "base64");
  const isCanonicalBase64 = /^[A-Za-z0-9+/]{43}=$/.test(value) &&
    decoded.toString("base64") === value;

  if (!isCanonicalBase64 || decoded.byteLength !== 32) {
    throw new Error(
      `${SERVER_ACTION_KEY_NAME} must be canonical base64 encoding exactly 32 bytes.`,
    );
  }
}

export function verifyServerActionEnvironment(environment) {
  verifyServerActionKey(environment[SERVER_ACTION_KEY_NAME]);
}

function main() {
  verifyServerActionEnvironment(process.env);
  process.stdout.write("Stable production Server Action encryption is configured.\n");
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
