/**
 * validate-pipeline
 *
 * Loads a Murmur pipeline-def YAML file, validates it against the M0 JSON Schema
 * (vendored at scripts/pipeline-def.schema.json), and optionally confirms every
 * referenced subcommand `endpoint` URL exists as a Next.js route under
 * apps/web/app/api/murmur/.
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

/**
 * Load and YAML-parse a pipeline-def file.
 *
 * @param path - Absolute path to a YAML file.
 * @returns The parsed document (untyped; caller must validate).
 * @throws on I/O or YAML parse failure.
 */
export function loadPipeline(path: string): unknown {
  throw new Error("not implemented");
}

/**
 * Validate a parsed document against the vendored M0 pipeline-def JSON Schema.
 *
 * @returns Discriminated union: `{ ok: true }` on success or
 *   `{ ok: false; errors }` with Ajv error objects on failure.
 */
export function validateSchema(
  doc: unknown,
): { ok: true } | { ok: false; errors: ReadonlyArray<{ instancePath: string; message?: string; keyword: string; params: unknown }> } {
  throw new Error("not implemented");
}

/**
 * Walk the pipeline def and return every endpoint URL referenced by subcommands.
 * Strips the "POST " prefix; returns just the URL.
 */
export function collectSubcommandEndpoints(doc: PipelineDef): string[] {
  throw new Error("not implemented");
}

/**
 * Walk the pipeline def and return the canonical ordered list of subcommand
 * NAMES (e.g. "probe monitor", "feedback") referenced anywhere in the doc.
 * De-duplicated, in order of first appearance.
 */
export function collectSubcommandNames(doc: PipelineDef): string[] {
  throw new Error("not implemented");
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
  throw new Error("not implemented");
}

/**
 * For each endpoint, confirm `<appDir>/<route-path>/route.{ts,tsx,js,mjs}` exists.
 *
 * @param appDir - root of the Next.js `app` directory (e.g. `<repo>/apps/web/app`).
 * @returns object listing missing endpoints; empty `missing` means all present.
 */
export function checkRoutes(
  endpoints: ReadonlyArray<string>,
  appDir: string,
): { ok: boolean; missing: string[] } {
  throw new Error("not implemented");
}

/**
 * CLI entry point. Parses argv, runs the pipeline, prints diagnostics, and
 * sets process.exitCode.
 */
export async function main(argv: ReadonlyArray<string>): Promise<number> {
  throw new Error("not implemented");
}
