import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveCrawlerDataContract,
  deriveCrawlerRuntimeContract,
  isCrawlerRuntimePath,
  isPublishableCrawlerDataPath,
} from "./derive-crawler-runtime-contract.mjs";

const entry = (path, oid) => ({ mode: "100644", type: "blob", oid, path });

test("runtime contract matches the crawler deploy boundary", () => {
  for (const path of [
    "apps/crawler/src/core/monitors/dom.py",
    "apps/crawler/deploy.sh",
    "apps/crawler/docker-compose.yml",
    "apps/crawler/VERSION",
    "apps/crawler/data/industries.csv",
    "apps/crawler/data/occupations.csv",
    "apps/crawler/data/seniority.csv",
    "apps/crawler/data/technologies.csv",
    ".github/workflows/deploy-crawler-browser.yml",
    "scripts/derive-crawler-runtime-contract.mjs",
  ]) {
    assert.equal(isCrawlerRuntimePath(path), true, path);
  }

  for (const path of [
    "apps/crawler/data/images/example/logo.png",
    "apps/crawler/traces/example.json",
    "apps/crawler/ws-package/pyproject.toml",
    "apps/crawler/README.md",
    "apps/crawler/docs/operator.md",
    ".github/workflows/sync-data.yml",
  ]) {
    assert.equal(isCrawlerRuntimePath(path), false, path);
  }
});

test("publishable data contract covers every crawler CSV but no image assets", () => {
  for (const path of [
    "apps/crawler/data/boards.csv",
    "apps/crawler/data/nested/example.csv",
    "apps/crawler/data/industries.csv",
    "apps/crawler/data/occupations.csv",
    "apps/crawler/data/seniority.csv",
    "apps/crawler/data/technologies.csv",
  ]) {
    assert.equal(isPublishableCrawlerDataPath(path), true, path);
  }
  for (const path of [
    "apps/crawler/data/images/example/logo.png",
    "apps/crawler/src/cli.py",
  ]) {
    assert.equal(isPublishableCrawlerDataPath(path), false, path);
  }
});

test("data-only changes preserve the runtime contract", () => {
  const runtime = entry("apps/crawler/src/cli.py", "a".repeat(40));
  const workflow = entry(
    ".github/workflows/deploy-crawler-browser.yml",
    "b".repeat(40),
  );
  const before = [
    runtime,
    workflow,
    entry("apps/crawler/data/boards.csv", "c".repeat(40)),
  ];
  const after = [
    runtime,
    workflow,
    entry("apps/crawler/data/boards.csv", "d".repeat(40)),
  ];

  assert.equal(
    deriveCrawlerRuntimeContract(before),
    deriveCrawlerRuntimeContract(after),
  );
});

test("runtime and rollout changes alter the contract", () => {
  const source = entry("apps/crawler/src/cli.py", "a".repeat(40));
  const workflow = entry(
    ".github/workflows/deploy-crawler-browser.yml",
    "b".repeat(40),
  );
  const baseline = deriveCrawlerRuntimeContract([source, workflow]);

  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      entry("apps/crawler/src/cli.py", "c".repeat(40)),
      workflow,
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      entry(".github/workflows/deploy-crawler-browser.yml", "d".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/industries.csv", "e".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/occupations.csv", "e".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/seniority.csv", "f".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/technologies.csv", "f".repeat(40)),
    ]),
  );
});

test("data contract changes for CSV content but ignores runtime-only advances", () => {
  const boards = entry("apps/crawler/data/boards.csv", "a".repeat(40));
  const companies = entry("apps/crawler/data/companies.csv", "b".repeat(40));
  const baseline = deriveCrawlerDataContract([
    boards,
    companies,
    entry("apps/crawler/src/cli.py", "c".repeat(40)),
  ]);

  assert.equal(
    baseline,
    deriveCrawlerDataContract([
      boards,
      companies,
      entry("apps/crawler/src/cli.py", "d".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerDataContract([
      entry("apps/crawler/data/boards.csv", "e".repeat(40)),
      companies,
    ]),
  );
});
