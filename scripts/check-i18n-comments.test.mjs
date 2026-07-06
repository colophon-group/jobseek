import assert from "node:assert/strict";
import test from "node:test";

import { findI18nCommentViolationsInSource } from "./check-i18n-comments.mjs";

test("flags i18n._ descriptors with ids but no translator comment", () => {
  const violations = findI18nCommentViolationsInSource(
    `
      const title = i18n._({
        id: "home.meta.title",
        message: "Track companies",
      });
    `,
    "apps/web/app/[lang]/page.tsx",
  );

  assert.deepEqual(violations, [
    {
      file: "apps/web/app/[lang]/page.tsx",
      line: 2,
      column: 28,
      id: "home.meta.title",
      message: "Track companies",
    },
  ]);
});

test("allows i18n._ descriptors with translator comments", () => {
  const violations = findI18nCommentViolationsInSource(
    `
      const title = i18n._({
        id: "home.meta.title",
        comment: "Metadata title for the public landing page.",
        message: "Track companies",
      });
    `,
    "apps/web/app/[lang]/page.tsx",
  );

  assert.deepEqual(violations, []);
});

test("ignores non-i18n descriptor objects and non-descriptor overloads", () => {
  const violations = findI18nCommentViolationsInSource(
    `
      const descriptor = { id: "plain.object", message: "Plain object" };
      const macro = t({ id: "macro.title", message: "Macro title" });
      const raw = i18n._("common.raw");
    `,
    "apps/web/src/example.ts",
  );

  assert.deepEqual(violations, []);
});

test("reports each missing comment in multiline JSON-LD arrays", () => {
  const violations = findI18nCommentViolationsInSource(
    `
      const features = [
        i18n._({ id: "app.schema.feature.monitor", message: "Monitor company career pages" }),
        i18n._({
          id: "app.schema.feature.alerts",
          message: "Real-time job posting alerts",
        }),
      ];
    `,
    "apps/web/app/[lang]/layout.tsx",
  );

  assert.deepEqual(
    violations.map(({ file, line, column, id }) => ({ file, line, column, id })),
    [
      {
        file: "apps/web/app/[lang]/layout.tsx",
        line: 3,
        column: 16,
        id: "app.schema.feature.monitor",
      },
      {
        file: "apps/web/app/[lang]/layout.tsx",
        line: 4,
        column: 16,
        id: "app.schema.feature.alerts",
      },
    ],
  );
});
