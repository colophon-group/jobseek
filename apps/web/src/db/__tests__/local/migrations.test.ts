import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const drizzleDir = path.resolve(process.cwd(), "drizzle");

function readMigrationSql() {
  return fs
    .readdirSync(drizzleDir)
    .filter((file) => file.endsWith(".sql"))
    .sort()
    .map((file) => fs.readFileSync(path.join(drizzleDir, file), "utf8"))
    .join("\n");
}

describe("job_board migrations", () => {
  it("include the columns crawler sync writes into the app database", () => {
    const sql = readMigrationSql();

    expect(sql).toContain("board_slug");
    expect(sql).toContain("board_status");
    expect(sql).toContain("throttle_key");
    expect(sql).toContain("scrape_interval_hours");
    expect(sql).toContain("monitor_needs_browser");
    expect(sql).toContain("scraper_needs_browser");
  });
});

describe("user_preferences migrations", () => {
  it("include the columns the authenticated bootstrap reads", () => {
    const sql = readMigrationSql();

    expect(sql).toContain("display_currency");
    expect(sql).toContain("dismissed_banners");
    expect(sql).toContain("salary_period");
  });
});
