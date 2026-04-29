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
 * Result of a single `POST /pipelines` round-trip from `postPipeline`.
 * `status` is the HTTP status; `body` is the parsed envelope, or a
 * stringified body on JSON-parse failure.
 */
export interface PostPipelineResult {
  readonly status: number;
  readonly body: MurmurOkResponse | MurmurErrResponse | { readonly raw: string };
}

/**
 * Streams used by `main`. Real CLI uses `process.stdout` /
 * `process.stderr`; tests use an in-memory accumulator.
 */
export interface WritableStreamLike {
  write(chunk: string): boolean | unknown;
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
export function readYamlRaw(_filePath: string): string {
  throw new Error("not implemented");
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
export function extractPipelineId(_yamlText: string): string {
  throw new Error("not implemented");
}

/**
 * Build the absolute URL for `POST /pipelines` given a base URL.
 * Tolerates trailing slashes on the base.
 */
export function buildPipelinesUrl(_baseUrl: string): string {
  throw new Error("not implemented");
}

/**
 * Validate that the env carries both required vars. Returns a list of
 * missing variable NAMES (not values). Empty list = OK.
 */
export function checkEnv(_env: RegisterEnv): string[] {
  throw new Error("not implemented");
}

/**
 * POST a pipeline def to Murmur. Returns the parsed envelope; does NOT
 * throw on non-2xx — caller decides how to react.
 *
 * @throws only on transport-level failure (the `fetch` call itself
 *   rejected, e.g. DNS or refused connection).
 */
export async function postPipeline(
  _url: string,
  _token: string,
  _body: RegisterRequestBody,
  _fetchImpl: FetchImpl,
): Promise<PostPipelineResult> {
  throw new Error("not implemented");
}

/**
 * Format a non-success response into a one-line stderr message. Never
 * includes the bearer token (the token is not on the response anyway,
 * but this is documented to anchor the test.)
 */
export function formatFailure(_result: PostPipelineResult): string {
  throw new Error("not implemented");
}

interface ParsedArgs {
  readonly yamlPath: string | null;
}

/**
 * Parse CLI argv (sans `node` and script). Single positional arg.
 */
export function parseArgs(_argv: ReadonlyArray<string>): ParsedArgs {
  throw new Error("not implemented");
}

/**
 * Programmable entry point. CLI calls this with `process.argv.slice(2)`,
 * `process.env`, global `fetch`, and the real stdio streams. Tests pass
 * stubs.
 *
 * @returns process exit code (0/1/2 per module docs).
 */
export async function main(
  _argv: ReadonlyArray<string>,
  _env: RegisterEnv,
  _fetchImpl: FetchImpl,
  _stdout: WritableStreamLike,
  _stderr: WritableStreamLike,
): Promise<number> {
  throw new Error("not implemented");
}
