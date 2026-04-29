/**
 * register-pipeline
 *
 * Registers a Murmur pipeline-def YAML against a running Murmur publisher
 * via authenticated `POST /pipelines`. Run once before demo rehearsal (and
 * any time the pipeline def changes).
 *
 * Body shape sent to Murmur (per docs/contracts.md §1 and M4's
 * `src/api/publisher/pipelines.ts`):
 *
 *   { "id": "<pipeline-id>", "def_yaml": "<raw YAML string>" }
 *
 * Murmur parses the YAML server-side and validates against its own
 * pipeline-def schema. This script does NOT validate the YAML — that
 * is `validate-pipeline`'s job (jobseek#2760, P1). The two scripts
 * deliberately split labour: validate locally, then register.
 *
 * Idempotence: M4 upserts on `id` (`INSERT ... ON CONFLICT(id) DO
 * UPDATE`). Running this script twice with the same YAML returns 200
 * both times.
 *
 * Usage:
 *   tsx register.ts <yaml-path>
 *
 * Required env:
 *   MURMUR_URL    Base URL, e.g. https://murmur.colophon-group.org
 *   MURMUR_TOKEN  Bearer token (per docs/contracts.md §2)
 *
 * Exit codes:
 *   0  — pipeline registered (HTTP 2xx with `{ ok: true }` envelope)
 *   1  — registration failed (non-2xx, network error, or `{ ok: false }`)
 *   2  — usage / I/O / config error (missing argv, missing env, unreadable file)
 *
 * NEVER LOGS THE TOKEN. The token value is only ever placed in the
 * outgoing `Authorization` header. Missing-env messages reference
 * variable names, not values.
 *
 * @module register-pipeline
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import YAML from "yaml";

/**
 * The minimum set of env vars this script needs. Both must be present
 * and non-empty.
 */
export interface RegisterEnv {
  readonly MURMUR_URL?: string | undefined;
  readonly MURMUR_TOKEN?: string | undefined;
}

/**
 * Injectable `fetch` for unit tests. Defaults to global `fetch` at
 * CLI invocation time.
 */
export type FetchImpl = typeof fetch;

/**
 * Outgoing body shape for `POST /pipelines`. Matches M4 exactly
 * (`src/api/publisher/pipelines.ts` PostPipelinesBody).
 */
export interface RegisterRequestBody {
  readonly id: string;
  readonly def_yaml: string;
}

/**
 * Murmur's success envelope for `POST /pipelines`.
 */
export interface MurmurOkResponse {
  readonly ok: true;
  readonly data?: { readonly id: string };
}

/**
 * Murmur's failure envelope.
 */
export interface MurmurErrResponse {
  readonly ok: false;
  readonly errors: ReadonlyArray<string | Record<string, unknown>>;
}

/**
 * Read a YAML file as raw text and return it verbatim. The string is
 * what we POST to Murmur — no JSON conversion locally (avoids
 * double-conversion bugs; server does its own YAML parse).
 *
 * @param filePath - Absolute path to a YAML file.
 * @returns The file contents as UTF-8 text.
 * @throws on I/O failure (file not found, unreadable).
 */
export function readYamlRaw(filePath: string): string {
  return readFileSync(filePath, "utf-8");
}

/**
 * Extract the top-level `id` field from a pipeline-def YAML string.
 * The Murmur `POST /pipelines` body requires `id` alongside the raw
 * YAML; we parse just enough locally to produce that field. The full
 * pipeline shape is validated server-side.
 *
 * @param yamlText - Raw YAML contents.
 * @returns The `id` value.
 * @throws if the YAML doesn't parse, isn't an object, or has no
 *   non-empty string `id`.
 */
export function extractPipelineId(yamlText: string): string {
  const parsed: unknown = YAML.parse(yamlText);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("pipeline-def YAML must parse to a top-level object");
  }
  const candidate = (parsed as { id?: unknown }).id;
  if (typeof candidate !== "string" || candidate.length === 0) {
    throw new Error("pipeline-def YAML missing required top-level `id`");
  }
  return candidate;
}

/**
 * Build the absolute URL for `POST /pipelines` given a base URL.
 * Tolerates trailing slashes on the base.
 */
export function buildPipelinesUrl(baseUrl: string): string {
  const trimmed = baseUrl.replace(/\/+$/, "");
  return `${trimmed}/pipelines`;
}

/**
 * Validate that the env carries both required vars. Returns a list of
 * missing variable NAMES (not values). Empty list = OK.
 */
export function checkEnv(env: RegisterEnv): string[] {
  const missing: string[] = [];
  if (!env.MURMUR_URL || env.MURMUR_URL.length === 0) missing.push("MURMUR_URL");
  if (!env.MURMUR_TOKEN || env.MURMUR_TOKEN.length === 0) missing.push("MURMUR_TOKEN");
  return missing;
}

/**
 * Result of a single `POST /pipelines` round-trip from `postPipeline`.
 * `status` is the HTTP status; `body` is the parsed envelope, or a
 * stringified body on JSON-parse failure.
 */
export interface PostPipelineResult {
  readonly status: number;
  readonly body: MurmurOkResponse | MurmurErrResponse | { readonly raw: string };
}

/**
 * POST a pipeline def to Murmur. Returns the parsed envelope; does NOT
 * throw on non-2xx — caller decides how to react.
 *
 * @throws only on transport-level failure (the `fetch` call itself
 *   rejected, e.g. DNS or refused connection).
 */
export async function postPipeline(
  url: string,
  token: string,
  body: RegisterRequestBody,
  fetchImpl: FetchImpl,
): Promise<PostPipelineResult> {
  const res = await fetchImpl(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let parsed: PostPipelineResult["body"];
  try {
    parsed = JSON.parse(text) as MurmurOkResponse | MurmurErrResponse;
  } catch {
    parsed = { raw: text };
  }
  return { status: res.status, body: parsed };
}

/**
 * Format a non-success response into a one-line stderr message. Never
 * includes the bearer token (the token is not on the response anyway,
 * but this is documented to anchor the test.)
 */
export function formatFailure(result: PostPipelineResult): string {
  const { status, body } = result;
  if ("ok" in body && body.ok === false) {
    return `register-pipeline: HTTP ${status}; errors=${JSON.stringify(body.errors)}`;
  }
  if ("ok" in body && body.ok === true) {
    // Should not occur on the failure path; defensive.
    return `register-pipeline: HTTP ${status}; unexpected ok=true on failure path`;
  }
  if ("raw" in body) {
    return `register-pipeline: HTTP ${status}; non-JSON body=${body.raw.slice(0, 200)}`;
  }
  return `register-pipeline: HTTP ${status}; unknown body shape`;
}

/**
 * Streams used by `main`. Real CLI uses `process.stdout` /
 * `process.stderr`; tests use an in-memory accumulator.
 */
export interface WritableStreamLike {
  write(chunk: string): boolean | unknown;
}

interface ParsedArgs {
  readonly yamlPath: string | null;
}

/**
 * Parse CLI argv (sans `node` and script). Single positional arg.
 */
export function parseArgs(argv: ReadonlyArray<string>): ParsedArgs {
  const positional: string[] = [];
  for (const arg of argv) {
    if (arg.startsWith("--")) {
      throw new Error(`unknown argument: ${arg}`);
    }
    positional.push(arg);
  }
  if (positional.length === 0) return { yamlPath: null };
  if (positional.length > 1) {
    throw new Error(
      `expected exactly one positional arg (yaml path); got ${positional.length}`,
    );
  }
  return { yamlPath: positional[0] ?? null };
}

/**
 * Programmable entry point. CLI calls this with `process.argv.slice(2)`,
 * `process.env`, global `fetch`, and the real stdio streams. Tests pass
 * stubs.
 *
 * @returns process exit code (0/1/2 per module docs).
 */
export async function main(
  argv: ReadonlyArray<string>,
  env: RegisterEnv,
  fetchImpl: FetchImpl,
  stdout: WritableStreamLike,
  stderr: WritableStreamLike,
): Promise<number> {
  // 1. Parse argv.
  let args: ParsedArgs;
  try {
    args = parseArgs(argv);
  } catch (e) {
    stderr.write(`register-pipeline: ${(e as Error).message}\n`);
    return 2;
  }
  if (!args.yamlPath) {
    stderr.write("register-pipeline: usage: register-pipeline <yaml-path>\n");
    return 2;
  }

  // 2. Env check (do this BEFORE reading the file so a misconfigured
  //    invocation fails fast; also makes the missing-env tests easy).
  const missing = checkEnv(env);
  if (missing.length > 0) {
    stderr.write(
      `register-pipeline: missing required env: ${missing.join(", ")}\n`,
    );
    return 2;
  }

  // 3. Read YAML once.
  const absPath = path.resolve(args.yamlPath);
  let yamlText: string;
  try {
    yamlText = readYamlRaw(absPath);
  } catch (e) {
    stderr.write(
      `register-pipeline: failed to read ${absPath}: ${(e as Error).message}\n`,
    );
    return 2;
  }

  // 4. Extract the top-level id locally (Murmur's body shape requires it
  //    alongside the raw YAML).
  let pipelineId: string;
  try {
    pipelineId = extractPipelineId(yamlText);
  } catch (e) {
    stderr.write(`register-pipeline: ${(e as Error).message}\n`);
    return 2;
  }

  // 5. POST to Murmur.
  // checkEnv already proved both vars are non-empty strings.
  const baseUrl = env.MURMUR_URL as string;
  const token = env.MURMUR_TOKEN as string;
  const url = buildPipelinesUrl(baseUrl);
  const body: RegisterRequestBody = { id: pipelineId, def_yaml: yamlText };

  let result: PostPipelineResult;
  try {
    result = await postPipeline(url, token, body, fetchImpl);
  } catch (e) {
    stderr.write(
      `register-pipeline: transport error POSTing to ${url}: ${(e as Error).message}\n`,
    );
    return 1;
  }

  // 6. Branch on status.
  if (result.status >= 200 && result.status < 300) {
    if ("ok" in result.body && result.body.ok === true) {
      stdout.write(
        `register-pipeline: registered pipeline id=${pipelineId} at ${url} (HTTP ${result.status})\n`,
      );
      return 0;
    }
    // 2xx but envelope says ok=false (or unparseable). Treat as failure.
    stderr.write(`${formatFailure(result)}\n`);
    return 1;
  }

  stderr.write(`${formatFailure(result)}\n`);
  return 1;
}

// Direct CLI execution.
if (
  typeof process !== "undefined" &&
  process.argv[1] !== undefined &&
  import.meta.url === `file://${process.argv[1]}`
) {
  // Stash to avoid passing `process.env` (which has many other keys we
  // don't need) deeper in. Only pull what we use.
  const env: RegisterEnv = {
    MURMUR_URL: process.env.MURMUR_URL,
    MURMUR_TOKEN: process.env.MURMUR_TOKEN,
  };
  // `fetch` is global on Node ≥ 18.
  const fetchImpl: FetchImpl = globalThis.fetch.bind(globalThis);
  main(process.argv.slice(2), env, fetchImpl, process.stdout, process.stderr).then(
    (code) => {
      process.exit(code);
    },
    (err) => {
      process.stderr.write(
        `register-pipeline: unexpected error: ${(err as Error).stack ?? err}\n`,
      );
      process.exit(2);
    },
  );
}

