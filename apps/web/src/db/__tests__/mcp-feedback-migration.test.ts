// @vitest-environment node
import { readFileSync } from "node:fs";
import { drizzle } from "drizzle-orm/postgres-js";
import { sql as query } from "drizzle-orm";
import { getTableConfig } from "drizzle-orm/pg-core";
import postgres from "postgres";
import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";
import { mcpFeedback } from "../schema";

const mocks = vi.hoisted(() => ({ insert: vi.fn() }));
vi.mock("@/db", () => ({ db: { insert: mocks.insert } }));
vi.mock("@/lib/rate-limit", () => ({ getClientIp: () => "203.0.113.1", feedbackLimiter: { limit: async () => ({ success: true }) } }));
vi.mock("@/lib/public-api-metrics", () => ({ recordPublicApiMetric: vi.fn() }));
vi.mock("next/server", async (importOriginal) => ({ ...await importOriginal<typeof import("next/server")>(), after: vi.fn() }));
import { POST } from "../../../app/api/v1/feedback/route";

const migration = readFileSync("drizzle/0102_mcp_feedback.sql", "utf8");
const url = process.env.MCP_FEEDBACK_TEST_DATABASE_URL;

describe("feedback persistence contract", () => {
  it("declares server-only storage without workflow or caller-identity columns", () => {
    const config = getTableConfig(mcpFeedback);
    expect(config.enableRLS).toBe(true);
    expect(config.columns.map(c => c.name).sort()).toEqual(["consumer", "created_at", "id", "kind", "payload", "server_version"]);
    expect(config.checks).toHaveLength(3);
    expect(migration).toContain("REVOKE ALL ON TABLE public.mcp_feedback FROM PUBLIC");
  });

  it.skipIf(!url)("applies the migration and stores each tool's submission through the real route", async () => {
    const parsed = new URL(url!);
    if (!["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname) || !parsed.pathname.endsWith("fixture")) {
      throw new Error("Feedback database tests require a loopback fixture database");
    }
    const sql = postgres(url!, { max: 1, prepare: false });
    const rollback = new Error("rollback feedback fixture");
    try {
      const database = drizzle(sql);
      try { await database.transaction(async tx => {
        await tx.execute(query.raw("DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;"));
        for (const statement of migration.split("--> statement-breakpoint")) await tx.execute(query.raw(statement));
        mocks.insert.mockImplementation((table) => tx.insert(table));
        const submissions = [
          { kind: "bug", goal: "Find roles", expected: "Matches", observed: "Error", impact: "blocked" },
          { kind: "feature", goal: "Compare salaries", capability: "Salary summaries", benefit: "Fewer calls", impact: "partial" },
          { kind: "feedback", goal: "Find roles", observation: "Useful links" },
        ];
        for (const input of [...submissions, submissions[2]]) {
          const response = await POST(new NextRequest("https://example.test/api/v1/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }));
          expect(response.status).toBe(200);
          expect(await response.json()).toEqual({ success: true });
        }
        const rows = await tx.execute(query`SELECT kind, payload, server_version, created_at FROM public.mcp_feedback`);
        expect(rows).toHaveLength(4);
        for (const input of submissions) {
          const { kind, ...payload } = input;
          expect(rows.find(row => row.kind === kind)?.payload).toEqual(payload);
        }
        expect(rows.every(row => row.server_version && row.created_at)).toBe(true);
        await tx.execute(query.raw("SAVEPOINT browser_read; SET LOCAL ROLE anon;"));
        await expect(tx.execute(query`SELECT * FROM public.mcp_feedback`)).rejects.toMatchObject({ cause: { code: "42501" } });
        await tx.execute(query.raw("ROLLBACK TO SAVEPOINT browser_read;"));
        throw rollback;
      }); } catch (error) { if (error !== rollback) throw error; }
    } finally { await sql.end(); }
  });
});
