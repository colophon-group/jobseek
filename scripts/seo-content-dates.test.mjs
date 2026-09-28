import assert from "node:assert/strict";
import test from "node:test";
import { checkBlogContentDate } from "../.github/scripts/check-seo-content-dates.mjs";

const post = (modified, body = "Original") =>
  `---\ndatePublished: "2026-05-07"\ndateModified: "${modified}"\n---\n${body}\n`;

test("rejects a changed article with stale dates, including a translation", () => {
  assert.throws(() => checkBlogContentDate(post("2026-05-07"), post("2026-05-07", "Updated"), "2026-09-28"));
  assert.throws(() => checkBlogContentDate(post("2026-05-07", "Prima"), post("2026-05-07", "Traduzione"), "2026-09-28"));
});
test("allows same-day edits and correcting historical metadata without changing content", () => {
  checkBlogContentDate(post("2026-05-07"), post("2026-09-28", "Updated"), "2026-09-28");
  checkBlogContentDate(post("2026-09-28"), post("2026-09-28", "Updated again"), "2026-09-28");
  checkBlogContentDate(post("2026-05-07"), post("2026-09-10"), "2026-09-28");
});
test("rejects future and impossible calendar dates", () => {
  assert.throws(() => checkBlogContentDate("", post("2027-01-01"), "2026-09-28"));
  assert.throws(() => checkBlogContentDate("", post("2026-02-30"), "2026-09-28"));
});
