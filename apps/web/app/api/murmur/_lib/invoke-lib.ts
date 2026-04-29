/**
 * Cross-language boundary: TS route → Python lib via subprocess.
 *
 * Pattern (a) per the J5 IPC decision (see `../README.md`). Each call
 * spawns a fresh Python interpreter that runs
 * `apps.crawler.src.workspace.lib.cli_shim`. The shim reads JSON on
 * stdin and writes a JSON envelope on stdout; the TS side parses that
 * envelope and forwards it.
 *
 * Why subprocess and not in-process: the lib pulls Playwright +
 * httpx + the entire crawler tree, which is Python-only. Spawning is
 * 200–500ms per call; for probe/run that's noise next to multi-second
 * Playwright work, and for select/feedback it's still well inside the
 * M0 15 s subcommand budget.
 *
 * The shim's stdout schema is exactly the M0 envelope shape:
 *
 *     {"ok": true,  "data": {...}}
 *     {"ok": false, "errors": ["..."]}
 *
 * Anything else (non-zero exit, parse failure, timeout) is mapped to
 * `{ ok: false, errors: ["internal_error"] }`. The unparsed stderr is
 * logged server-side; never returned to the agent.
 *
 * @see colophon-group/jobseek#2759
 */

import { spawn } from "node:child_process";
import path from "node:path";

/**
 * Subcommand identifiers — must match the dispatch keys in
 * `cli_shim.py`. The string is what the shim looks up to choose which
 * Python function to call.
 */
export type LibSubcommand =
  | "probe_monitor"
  | "run_monitor"
  | "probe_scraper"
  | "run_scraper"
  | "select_monitor"
  | "select_scraper"
  | "feedback";

export interface InvokeLibResult {
  readonly ok: boolean;
  readonly data?: unknown;
  readonly errors?: readonly string[];
}

/**
 * Invocation injection seam. Tests pass a stub function so they don't
 * fork a Python process. Production routes pass the real
 * `defaultInvoker`. See `_lib/invoke-lib-default.ts` for the live impl.
 */
export type LibInvoker = (
  subcommand: LibSubcommand,
  body: unknown,
  claim_token: string,
) => Promise<InvokeLibResult>;

/**
 * Default invoker — spawns the Python shim with a 30 s wallclock cap.
 *
 * Environment:
 *   - `MURMUR_PY` (optional)        — path to the Python interpreter.
 *                                     Defaults to `python3`.
 *   - `MURMUR_CRAWLER_ROOT` (opt)   — path to the crawler app root.
 *                                     Defaults to `<repo>/apps/crawler`.
 *   - `MURMUR_DB_DSN` (required)    — Postgres DSN forwarded to the
 *                                     shim so it can build a
 *                                     `PostgresClaimKV`.
 *   - `MURMUR_INVOKE_TIMEOUT_MS` (opt) — wallclock cap; defaults to
 *                                     30000.
 */
export const defaultInvoker: LibInvoker = async (
  subcommand,
  body,
  claim_token,
) => {
  const interpreter = process.env.MURMUR_PY ?? "python3";
  const crawlerRoot =
    process.env.MURMUR_CRAWLER_ROOT ??
    path.resolve(process.cwd(), "../crawler");
  const dsn = process.env.MURMUR_DB_DSN ?? "";
  const timeoutMs = Number(process.env.MURMUR_INVOKE_TIMEOUT_MS ?? 30000);

  const payload = JSON.stringify({
    subcommand,
    body,
    claim_token,
    db_dsn: dsn,
  });

  return new Promise<InvokeLibResult>((resolve) => {
    const child = spawn(interpreter, ["-m", "src.workspace.lib.cli_shim"], {
      cwd: crawlerRoot,
      env: { ...process.env, PYTHONPATH: crawlerRoot, MURMUR_DB_DSN: dsn },
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let settled = false;

    const finish = (result: InvokeLibResult) => {
      if (settled) return;
      settled = true;
      try {
        child.kill("SIGKILL");
      } catch {
        // process may have already exited
      }
      resolve(result);
    };

    const timer = setTimeout(() => {
      // Log so operators can see why a request got `internal_error`.
      // eslint-disable-next-line no-console
      console.error(
        `[murmur invoke-lib] ${subcommand} timed out after ${timeoutMs}ms`,
      );
      finish({ ok: false, errors: ["internal_error"] });
    }, timeoutMs);

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });

    child.on("error", (err) => {
      clearTimeout(timer);
      // eslint-disable-next-line no-console
      console.error(
        `[murmur invoke-lib] ${subcommand} spawn failed: ${err.message}`,
      );
      finish({ ok: false, errors: ["internal_error"] });
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      if (settled) return;
      if (code !== 0) {
        // eslint-disable-next-line no-console
        console.error(
          `[murmur invoke-lib] ${subcommand} exited with code ${code}; stderr: ${stderr}`,
        );
        finish({ ok: false, errors: ["internal_error"] });
        return;
      }
      try {
        const parsed: unknown = JSON.parse(stdout);
        if (!isInvokeResult(parsed)) {
          // eslint-disable-next-line no-console
          console.error(
            `[murmur invoke-lib] ${subcommand} returned non-envelope stdout: ${stdout.slice(0, 200)}`,
          );
          finish({ ok: false, errors: ["internal_error"] });
          return;
        }
        finish(parsed);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(
          `[murmur invoke-lib] ${subcommand} stdout JSON parse error: ${(err as Error).message}; stdout=${stdout.slice(0, 200)}`,
        );
        finish({ ok: false, errors: ["internal_error"] });
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
};

function isInvokeResult(v: unknown): v is InvokeLibResult {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  if (typeof o.ok !== "boolean") return false;
  if (
    o.errors !== undefined &&
    (!Array.isArray(o.errors) || o.errors.some((e) => typeof e !== "string"))
  ) {
    return false;
  }
  return true;
}

// ── Test seam: route handlers read this overridable holder. ─────────

/**
 * Container for the active invoker. Tests overwrite `current` with a
 * stub before exercising a route; production code never reassigns it
 * away from `defaultInvoker`.
 */
export const InvokerHolder: { current: LibInvoker } = {
  current: defaultInvoker,
};

/** Convenience pass-through used by route handlers. */
export function invokeLib(
  subcommand: LibSubcommand,
  body: unknown,
  claim_token: string,
): Promise<InvokeLibResult> {
  return InvokerHolder.current(subcommand, body, claim_token);
}
