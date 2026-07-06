import assert from "node:assert/strict";
import test from "node:test";

import { classifyChanges, isNonCodePath } from "./ci-classify-changes.mjs";

test("classifies Markdown-only and docs-only changes as non-code", () => {
  assert.deepEqual(
    classifyChanges([
      "README.md",
      "apps/crawler/AGENTS.md",
      "docs/16-hetzner-maintenance.md",
      "docs/images/diagram.png",
    ]),
    {
      code: false,
      crawler_code: false,
      boards_csv: false,
    },
  );
});

test("classifies Dependabot and issue template metadata as non-code", () => {
  assert.deepEqual(
    classifyChanges([
      ".github/dependabot.yml",
      ".github/dependabot.yaml",
      ".github/ISSUE_TEMPLATE/bug.yml",
      ".github/DISCUSSION_TEMPLATE/idea.yml",
    ]),
    {
      code: false,
      crawler_code: false,
      boards_csv: false,
    },
  );
});

test("keeps workflow and script changes in the code lane", () => {
  assert.deepEqual(
    classifyChanges([
      ".github/workflows/ci.yml",
      ".github/scripts/close-linked-company-request-issues.sh",
      "scripts/ci-classify-changes.mjs",
    ]),
    {
      code: true,
      crawler_code: false,
      boards_csv: false,
    },
  );
});

test("marks crawler source changes as crawler code", () => {
  assert.deepEqual(classifyChanges(["apps/crawler/src/cli.py"]), {
    code: true,
    crawler_code: true,
    boards_csv: false,
  });
});

test("keeps crawler data and traces out of the code lane", () => {
  assert.deepEqual(
    classifyChanges([
      "apps/crawler/data/companies.csv",
      "apps/crawler/data/images/acme.png",
      "apps/crawler/traces/acme/raw.html",
    ]),
    {
      code: false,
      crawler_code: false,
      boards_csv: false,
    },
  );
});

test("flags boards.csv for targeted board probes without full code CI", () => {
  assert.deepEqual(classifyChanges(["apps/crawler/data/boards.csv"]), {
    code: false,
    crawler_code: false,
    boards_csv: true,
  });
});

test("uses the force sentinel when file discovery fails", () => {
  assert.deepEqual(classifyChanges(["force"]), {
    code: true,
    crawler_code: true,
    boards_csv: false,
  });
});

test("does not let non-code files hide code in mixed changes", () => {
  assert.deepEqual(
    classifyChanges(["docs/runbook.md", "apps/web/src/app/page.tsx"]),
    {
      code: true,
      crawler_code: false,
      boards_csv: false,
    },
  );
});

test("documents individual non-code path predicates", () => {
  assert.equal(isNonCodePath(".github/dependabot.yml"), true);
  assert.equal(isNonCodePath("apps/crawler/data/boards.csv"), true);
  assert.equal(isNonCodePath(".github/workflows/ci.yml"), false);
});
