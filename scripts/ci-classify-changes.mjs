import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const FORCE_SENTINEL = "force";

function isUnder(path, prefix) {
  return path === prefix || path.startsWith(`${prefix}/`);
}

export function isNonCodePath(path) {
  if (path.endsWith(".md")) {
    return true;
  }

  if (isUnder(path, "docs")) {
    return true;
  }

  if (path === ".github/dependabot.yml" || path === ".github/dependabot.yaml") {
    return true;
  }

  if (isUnder(path, ".github/ISSUE_TEMPLATE")) {
    return true;
  }

  if (isUnder(path, ".github/DISCUSSION_TEMPLATE")) {
    return true;
  }

  if (isUnder(path, "apps/crawler/data")) {
    return true;
  }

  if (isUnder(path, "apps/crawler/traces")) {
    return true;
  }

  if (path === "apps/crawler/VERSION") {
    return true;
  }

  return false;
}

export function classifyChanges(files) {
  const result = {
    code: false,
    crawler_code: false,
    boards_csv: false,
  };

  for (const rawFile of files) {
    const file = rawFile.trim();
    if (!file) {
      continue;
    }

    if (file === FORCE_SENTINEL) {
      result.code = true;
      result.crawler_code = true;
      continue;
    }

    if (file === "apps/crawler/data/boards.csv") {
      result.boards_csv = true;
      continue;
    }

    if (isNonCodePath(file)) {
      continue;
    }

    if (isUnder(file, "apps/crawler")) {
      result.code = true;
      result.crawler_code = true;
      continue;
    }

    result.code = true;
  }

  return result;
}

function readInputFiles() {
  if (process.argv.length > 2) {
    return process.argv.slice(2);
  }

  return readFileSync(0, "utf8").split(/\r?\n/);
}

function printOutput(result) {
  for (const [key, value] of Object.entries(result)) {
    console.log(`${key}=${value ? "true" : "false"}`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  printOutput(classifyChanges(readInputFiles()));
}
