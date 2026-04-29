/**
 * validate-pipeline
 *
 * Loads a Murmur pipeline-def YAML file, validates it against the M0 JSON Schema
 * (vendored at scripts/pipeline-def.schema.json), and optionally confirms every
 * referenced subcommand `endpoint` URL exists as a Next.js route under
 * apps/murmur-shim/app/api/murmur/.
 *
 * Usage:
 *   tsx validate-pipeline.ts <yaml-path> [--no-routes-check] [--app-dir <path>]
 *
 * Exit codes:
 *   0  — schema valid (and routes valid, if checked)
 *   1  — schema invalid OR a referenced route does not exist
 *   2  — usage / I/O error
 *
 * NOTE: --no-routes-check is a temporary escape hatch while jobseek#2759 (J5)
 *   has not yet landed the /api/murmur/* routes. Remove this flag and make
 *   route checking mandatory once #2759 merges.
 *
 * @module validate-pipeline
 */
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020, { type ErrorObject } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import YAML from "yaml";

/**
 * The parsed pipeline-def document (loose shape; full validation is done by Ajv).
 */
export interface PipelineDef {
  id: string;
  version?: number;
  initial_input: Record<string, unknown>;
  subtasks: Array<{
    id: string;
    instructions: string;
    inputs?: Array<{ from: string; path?: string }>;
    output_schema: Record<string, unknown>;
    subcommands?: Array<{
      name: string;
      endpoint: string;
      input_schema?: Record<string, unknown>;
    }>;
    spawns?: { for_each: string; template: string };
    requires?: string[];
    skip_if?: Record<string, unknown>;
  }>;
  final_output: {
    composes: string[];
    webhook: string;
  };
}

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCHEMA_PATH = path.join(HERE, "pipeline-def.schema.json");

/**
 * Load and YAML-parse a pipeline-def file.
 *
 * @param filePath - Absolute path to a YAML file.
 * @returns The parsed document (untyped; caller must validate).
 * @throws on I/O or YAML parse failure.
 */
export function loadPipeline(filePath: string): unknown {
  const raw = readFileSync(filePath, "utf-8");
  return YAML.parse(raw);
}

type AjvValidator = ((doc: unknown) => boolean) & { errors?: ErrorObject[] | null };

let cachedValidator: AjvValidator | null = null;

function getValidator(): AjvValidator {
  if (cachedValidator) return cachedValidator;
  const schema = JSON.parse(readFileSync(SCHEMA_PATH, "utf-8"));
  // Strip $id so Ajv doesn't try to network-resolve it; we compile in-process.
  delete (schema as { $id?: string }).$id;
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  const compiled = ajv.compile(schema) as unknown as AjvValidator;
  cachedValidator = compiled;
  return compiled;
}

/**
 * Validate a parsed document against the vendored M0 pipeline-def JSON Schema.
 */
export function validateSchema(
  doc: unknown,
):
  | { ok: true }
  | {
      ok: false;
      errors: ReadonlyArray<{
        instancePath: string;
        message?: string;
        keyword: string;
        params: unknown;
      }>;
    } {
  const validator = getValidator();
  const valid = validator(doc);
  if (valid) return { ok: true };
  const errs = (validator.errors ?? []).map((e) => ({
    instancePath: e.instancePath,
    message: e.message,
    keyword: e.keyword,
    params: e.params,
  }));
  return { ok: false, errors: errs };
}

/**
 * Walk the pipeline def and return every endpoint URL referenced by subcommands.
 * Strips the "POST " prefix; returns just the URL.
 */
export function collectSubcommandEndpoints(doc: PipelineDef): string[] {
  const out: string[] = [];
  for (const subtask of doc.subtasks ?? []) {
    for (const sc of subtask.subcommands ?? []) {
      const m = /^POST\s+(\S+)$/.exec(sc.endpoint ?? "");
      if (m && m[1]) out.push(m[1]);
    }
  }
  return out;
}

/**
 * Walk the pipeline def and return the canonical ordered list of subcommand
 * NAMES (e.g. "probe monitor", "feedback") referenced anywhere in the doc.
 * De-duplicated, in order of first appearance.
 */
export function collectSubcommandNames(doc: PipelineDef): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const subtask of doc.subtasks ?? []) {
    for (const sc of subtask.subcommands ?? []) {
      if (sc.name && !seen.has(sc.name)) {
        seen.add(sc.name);
        out.push(sc.name);
      }
    }
  }
  return out;
}

/**
 * Translate an endpoint URL like
 *   https://jobseek.colophon-group.org/api/murmur/probes/monitor
 * into the relative Next.js App Router route path
 *   /api/murmur/probes/monitor
 *
 * @returns the route path, or null if the URL doesn't look like a jobseek api route.
 */
export function endpointToRoutePath(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.host !== "jobseek.colophon-group.org") return null;
  if (!parsed.pathname.startsWith("/api/")) return null;
  // Strip trailing slash for canonical comparison.
  return parsed.pathname.replace(/\/+$/, "");
}

const ROUTE_FILES = ["route.ts", "route.tsx", "route.js", "route.mjs"];

/**
 * For each endpoint, confirm `<appDir>/<route-path>/route.{ts,tsx,js,mjs}` exists.
 *
 * @param appDir - root of the Next.js `app` directory (e.g. `<repo>/apps/murmur-shim/app`).
 * @returns object listing missing endpoints; empty `missing` means all present.
 */
export function checkRoutes(
  endpoints: ReadonlyArray<string>,
  appDir: string,
): { ok: boolean; missing: string[] } {
  const missing: string[] = [];
  for (const url of endpoints) {
    const routePath = endpointToRoutePath(url);
    if (routePath === null) {
      missing.push(url);
      continue;
    }
    // routePath starts with '/'; strip leading slash for join.
    const relative = routePath.replace(/^\/+/, "");
    const dir = path.join(appDir, relative);
    const found = ROUTE_FILES.some((name) => existsSync(path.join(dir, name)));
    if (!found) missing.push(url);
  }
  return { ok: missing.length === 0, missing };
}

interface ParsedArgs {
  yamlPath: string | null;
  noRoutesCheck: boolean;
  appDir: string | null;
}

function parseArgs(argv: ReadonlyArray<string>): ParsedArgs {
  const result: ParsedArgs = { yamlPath: null, noRoutesCheck: false, appDir: null };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--no-routes-check") {
      result.noRoutesCheck = true;
    } else if (arg === "--app-dir") {
      const next = argv[i + 1];
      if (!next) throw new Error("--app-dir requires a path argument");
      result.appDir = next;
      i++;
    } else if (arg && !arg.startsWith("--") && result.yamlPath === null) {
      result.yamlPath = arg;
    } else if (arg) {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return result;
}

function defaultAppDir(yamlPath: string): string {
  // From .../apps/crawler/murmur/pipelines/<file>.yaml, resolve back to
  // apps/murmur-shim/app (where the M0 routes live as of jobseek#2773).
  const repoRoot = path.resolve(path.dirname(yamlPath), "..", "..", "..", "..");
  return path.join(repoRoot, "apps", "murmur-shim", "app");
}

function formatErrors(
  errors: ReadonlyArray<{
    instancePath: string;
    message?: string;
    keyword: string;
    params: unknown;
  }>,
): string {
  return errors
    .map((e) => {
      const where = e.instancePath || "(root)";
      const detail = JSON.stringify(e.params);
      return `  ${where}  [${e.keyword}]  ${e.message ?? ""}  params=${detail}`;
    })
    .join("\n");
}

/**
 * CLI entry point.
 */
export async function main(argv: ReadonlyArray<string>): Promise<number> {
  let args: ParsedArgs;
  try {
    args = parseArgs(argv);
  } catch (e) {
    process.stderr.write(`validate-pipeline: ${(e as Error).message}\n`);
    return 2;
  }
  if (!args.yamlPath) {
    process.stderr.write(
      "validate-pipeline: usage: validate-pipeline <yaml-path> [--no-routes-check] [--app-dir <path>]\n",
    );
    return 2;
  }

  const yamlPath = path.resolve(args.yamlPath);
  let doc: unknown;
  try {
    doc = loadPipeline(yamlPath);
  } catch (e) {
    process.stderr.write(`validate-pipeline: failed to load ${yamlPath}: ${(e as Error).message}\n`);
    return 2;
  }

  const result = validateSchema(doc);
  if (!result.ok) {
    process.stderr.write(`validate-pipeline: schema validation FAILED for ${yamlPath}\n`);
    process.stderr.write(`${formatErrors(result.errors)}\n`);
    return 1;
  }

  process.stdout.write(`validate-pipeline: schema OK (${yamlPath})\n`);

  if (args.noRoutesCheck) {
    process.stdout.write(
      "validate-pipeline: route existence skipped (--no-routes-check); remove this flag once jobseek#2759 ships\n",
    );
    return 0;
  }

  const appDir = args.appDir ? path.resolve(args.appDir) : defaultAppDir(yamlPath);
  const endpoints = collectSubcommandEndpoints(doc as PipelineDef);
  const routes = checkRoutes(endpoints, appDir);
  if (!routes.ok) {
    process.stderr.write(
      `validate-pipeline: route existence FAILED — ${routes.missing.length} endpoint(s) have no matching Next.js route under ${appDir}:\n`,
    );
    for (const m of routes.missing) process.stderr.write(`  ${m}\n`);
    return 1;
  }
  process.stdout.write(`validate-pipeline: ${endpoints.length} route(s) OK under ${appDir}\n`);
  return 0;
}

// Direct execution (tsx ./validate-pipeline.ts ...).
if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2)).then(
    (code) => {
      process.exit(code);
    },
    (err) => {
      process.stderr.write(`validate-pipeline: unexpected error: ${(err as Error).stack ?? err}\n`);
      process.exit(2);
    },
  );
}
