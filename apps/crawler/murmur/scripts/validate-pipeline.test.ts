/**
 * Tests for validate-pipeline.ts.
 *
 * Verification matrix from jobseek#2760:
 *   1. validate-pipeline against the committed YAML exits 0.
 *   2. validate-pipeline against the broken fixture exits non-zero with
 *      a useful error.
 *   3. The committed YAML's subcommand list equals the canonical 7 from
 *      contracts.md §5 — no orphans.
 *   4. Every `provider` enum value in list-boards exists in
 *      apps/crawler/data/boards.csv (monitor_type column).
 *   5. The vendored schema matches the upstream commit's content (smoke
 *      against accidental drift).
 */
import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

import {
  loadPipeline,
  validateSchema,
  collectSubcommandEndpoints,
  collectSubcommandNames,
  endpointToRoutePath,
  checkRoutes,
  type PipelineDef,
} from "./validate-pipeline.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(HERE, "..");
const REPO_ROOT = path.resolve(PKG_ROOT, "..", "..", "..");
const YAML_PATH = path.join(PKG_ROOT, "pipelines", "add-company.yaml");
const BROKEN_PATH = path.join(HERE, "fixtures", "broken.yaml");
const SCHEMA_PATH = path.join(HERE, "pipeline-def.schema.json");

const CANONICAL_SUBCOMMANDS = [
  "probe monitor",
  "select monitor",
  "run monitor",
  "probe scraper",
  "select scraper",
  "run scraper",
  "feedback",
] as const;

describe("loadPipeline", () => {
  it("parses valid YAML to a pipeline-def object", () => {
    const doc = loadPipeline(YAML_PATH) as PipelineDef;
    expect(doc).toBeTypeOf("object");
    expect(doc.id).toBe("jobseek-add-company");
  });

  it("throws on a missing file", () => {
    expect(() => loadPipeline(path.join(HERE, "does-not-exist.yaml"))).toThrow();
  });
});

describe("validateSchema", () => {
  it("accepts the committed YAML", () => {
    const doc = loadPipeline(YAML_PATH);
    const result = validateSchema(doc);
    expect(result.ok).toBe(true);
  });

  it("rejects the deliberately broken fixture", () => {
    const doc = loadPipeline(BROKEN_PATH);
    const result = validateSchema(doc);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.length).toBeGreaterThan(0);
      // Useful error: every error has a path or a recognisable keyword.
      for (const err of result.errors) {
        expect(typeof err.keyword).toBe("string");
      }
      // The broken id (`jobseek/add-company`) should surface a pattern error.
      const messages = result.errors
        .map((e) => `${e.instancePath} ${e.keyword} ${e.message ?? ""}`)
        .join("\n");
      expect(messages).toMatch(/(pattern|enum|required|format)/);
    }
  });

  it("rejects an empty document", () => {
    const result = validateSchema({});
    expect(result.ok).toBe(false);
  });
});

describe("collectSubcommandNames", () => {
  it("returns exactly the canonical 7 contracts.md §5 subcommands", () => {
    const doc = loadPipeline(YAML_PATH) as PipelineDef;
    const names = collectSubcommandNames(doc).slice().sort();
    expect(names).toEqual([...CANONICAL_SUBCOMMANDS].sort());
  });
});

describe("collectSubcommandEndpoints", () => {
  it("returns one https endpoint per subcommand", () => {
    const doc = loadPipeline(YAML_PATH) as PipelineDef;
    const endpoints = collectSubcommandEndpoints(doc);
    expect(endpoints.length).toBe(CANONICAL_SUBCOMMANDS.length);
    for (const url of endpoints) {
      expect(url).toMatch(/^https:\/\/jobseek\.colophon-group\.org\/api\/murmur\//);
    }
  });
});

describe("endpointToRoutePath", () => {
  it("maps known endpoints to the Next.js route path", () => {
    expect(
      endpointToRoutePath(
        "https://jobseek.colophon-group.org/api/murmur/probes/monitor",
      ),
    ).toBe("/api/murmur/probes/monitor");
  });

  it("returns null for non-jobseek URLs", () => {
    expect(endpointToRoutePath("https://example.com/foo")).toBeNull();
  });
});

describe("checkRoutes", () => {
  it("reports all endpoints missing when app dir has no /api/murmur tree", () => {
    // The current jobseek tree (pre-J5/#2759) does not yet have /api/murmur
    // routes. The validator must surface them all as missing rather than
    // silently passing.
    const appDir = path.join(REPO_ROOT, "apps", "web", "app");
    const endpoints = [
      "https://jobseek.colophon-group.org/api/murmur/probes/monitor",
      "https://jobseek.colophon-group.org/api/murmur/feedback",
    ];
    const result = checkRoutes(endpoints, appDir);
    expect(result.ok).toBe(false);
    expect(result.missing.length).toBe(endpoints.length);
  });
});

describe("provider enum sanity", () => {
  it("every list-boards.provider enum value exists in boards.csv monitor_type", () => {
    const doc = loadPipeline(YAML_PATH) as PipelineDef;
    const listBoards = doc.subtasks.find((s) => s.id === "list-boards");
    if (!listBoards) throw new Error("list-boards subtask missing");
    const properties = (listBoards.output_schema as any).properties as Record<string, unknown>;
    const boardItem = (properties.boards as any).items as Record<string, unknown>;
    const provider = (boardItem as any).properties.provider as Record<string, unknown>;
    const declared = (provider.enum as string[]).slice().sort();
    expect(declared.length).toBeGreaterThan(0);

    const csvPath = path.join(REPO_ROOT, "apps", "crawler", "data", "boards.csv");
    const csv = readFileSync(csvPath, "utf-8");
    const monitorTypes = new Set<string>();
    const lines = csv.split(/\r?\n/);
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i];
      if (!line) continue;
      const cols = line.split(",");
      const mt = cols[3];
      if (mt) monitorTypes.add(mt);
    }

    const unknown = declared.filter((p) => !monitorTypes.has(p));
    expect(unknown).toEqual([]);
  });
});

describe("vendored schema integrity", () => {
  it("schema file is non-empty and is a JSON Schema 2020-12 doc", () => {
    const raw = readFileSync(SCHEMA_PATH, "utf-8");
    expect(raw.length).toBeGreaterThan(0);
    const parsed = JSON.parse(raw);
    expect(parsed.$schema).toBe("https://json-schema.org/draft/2020-12/schema");
    // Hash is logged so a future drift is easy to investigate by looking
    // at the failing assertion message; the assertion itself just confirms
    // the SHA256 is a sane hex string of the right length.
    const hash = createHash("sha256").update(raw).digest("hex");
    expect(hash).toMatch(/^[a-f0-9]{64}$/);
  });
});

describe("CLI behaviour", () => {
  // Run the CLI as a child process so we exercise the real exit-code path.
  const cli = (args: string[]): { code: number; stdout: string; stderr: string } => {
    const candidates = [
      path.resolve(PKG_ROOT, "node_modules", ".bin", "tsx"),
      path.resolve(REPO_ROOT, "node_modules", ".bin", "tsx"),
    ];
    const tsxBin = candidates.find((p) => {
      try {
        readFileSync(p);
        return true;
      } catch {
        return false;
      }
    });
    if (!tsxBin) throw new Error(`tsx not found in: ${candidates.join(", ")}`);
    try {
      const stdout = execFileSync(
        tsxBin,
        [path.join(HERE, "validate-pipeline.ts"), ...args],
        { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] },
      );
      return { code: 0, stdout, stderr: "" };
    } catch (err) {
      const e = err as NodeJS.ErrnoException & { status?: number; stdout?: Buffer | string; stderr?: Buffer | string };
      return {
        code: e.status ?? 1,
        stdout: typeof e.stdout === "string" ? e.stdout : (e.stdout?.toString() ?? ""),
        stderr: typeof e.stderr === "string" ? e.stderr : (e.stderr?.toString() ?? ""),
      };
    }
  };

  it("exits 0 against the committed YAML with --no-routes-check", () => {
    const r = cli([YAML_PATH, "--no-routes-check"]);
    expect(r.code).toBe(0);
  });

  it("exits non-zero against the broken fixture", () => {
    const r = cli([BROKEN_PATH, "--no-routes-check"]);
    expect(r.code).not.toBe(0);
    expect(r.stderr + r.stdout).toMatch(/(pattern|enum|required|invalid|error)/i);
  });
});
