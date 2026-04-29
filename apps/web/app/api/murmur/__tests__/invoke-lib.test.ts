/**
 * Direct unit tests for the `defaultInvoker` subprocess branches.
 *
 * The route-level tests in `routes.test.ts` stub the invoker because
 * they care about the HTTP shim behaviour, not the subprocess wiring.
 * These tests do the inverse: drive `defaultInvoker` with carefully
 * chosen "interpreters" so we exercise each non-happy branch
 * deterministically without forking a real Python.
 *
 * We point `MURMUR_PY` at:
 *   - `false` (path probed)  — exits 1 immediately (non-zero exit branch)
 *   - `/bin/echo`            — exits 0 but stdout is "-m src.workspace.lib.cli_shim",
 *                              not valid JSON (parse-failure branch)
 *   - a temp shell script    — runs `while :; do sleep 1; done` (timeout branch)
 *   - a path that doesn't exist — `child.on('error')` (spawn-error branch)
 *
 * Happy-path coverage lives in `e2e.test.ts` against a real Python.
 * We still set `MURMUR_CRAWLER_ROOT` to a real directory because
 * Node's `spawn` validates `cwd`.
 *
 * @see colophon-group/jobseek#2759
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import os from "node:os";

import { defaultInvoker } from "../_lib/invoke-lib";

const ORIGINAL_ENV = { ...process.env };

beforeEach(() => {
  // Make all spawn-cwds resolve to a real directory.
  process.env.MURMUR_CRAWLER_ROOT = os.tmpdir();
  process.env.MURMUR_DB_DSN = "";
  // Default short timeout so the timeout test doesn't drag.
  process.env.MURMUR_INVOKE_TIMEOUT_MS = "1000";
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

describe("defaultInvoker subprocess branches", () => {
  it("maps a non-zero exit to { ok: false, errors: ['internal_error'] }", async () => {
    // `false` lives at /usr/bin/false on macOS and /bin/false on Linux;
    // probe both.
    const fs = await import("node:fs/promises");
    let falsePath = "/usr/bin/false";
    try {
      await fs.access(falsePath);
    } catch {
      falsePath = "/bin/false";
    }
    process.env.MURMUR_PY = falsePath;
    const result = await defaultInvoker("probe_monitor", {}, "claim-x");
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual(["internal_error"]);
  });

  it("maps non-envelope stdout to { ok: false, errors: ['internal_error'] }", async () => {
    // /bin/echo writes an arg to stdout and exits 0. The invoker passes
    // `-m src.workspace.lib.cli_shim` as argv, so echo prints those.
    // That's not valid JSON → parse fails → internal_error.
    process.env.MURMUR_PY = "/bin/echo";
    const result = await defaultInvoker("probe_monitor", {}, "claim-x");
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual(["internal_error"]);
  });

  it("times out long-running children and returns internal_error", async () => {
    // We need a child that (a) ignores the hardcoded `-m
    // src.workspace.lib.cli_shim` argv and (b) never exits on its own.
    // `/bin/cat` with no args reads stdin until EOF and writes it back.
    // The invoker calls `child.stdin.end()` after writing the payload,
    // which would let cat exit promptly — but we want to test the
    // wallclock-timeout branch, not the EOF-exit branch.
    //
    // Use `node -e <forever>`: node treats `-m ...` as positional args
    // (warning + ignored under -e) and the script runs until killed.
    //
    // The invoker hardcodes argv to `["-m", "src.workspace.lib.cli_shim"]`.
    // With node, this becomes `node -m src.workspace.lib.cli_shim` —
    // node interprets `-m` as an unknown short flag and exits 9 with
    // an error. So node is no good either.
    //
    // Cleanest alternative: a tiny shell script that loops forever and
    // ignores all args. Write it to a tempfile.
    const fs = await import("node:fs/promises");
    const path = await import("node:path");
    const tmpScript = path.join(os.tmpdir(), `murmur-sleep-${Date.now()}.sh`);
    await fs.writeFile(
      tmpScript,
      "#!/bin/sh\nwhile :; do sleep 1; done\n",
      { mode: 0o755 },
    );
    try {
      process.env.MURMUR_PY = tmpScript;
      process.env.MURMUR_INVOKE_TIMEOUT_MS = "300";
      const start = Date.now();
      const result = await defaultInvoker("probe_monitor", {}, "claim-x");
      const elapsed = Date.now() - start;
      expect(result.ok).toBe(false);
      expect(result.errors).toEqual(["internal_error"]);
      // The timer fires at 300 ms; allow slack for CI jitter.
      expect(elapsed).toBeGreaterThanOrEqual(250);
      expect(elapsed).toBeLessThan(5000);
    } finally {
      await fs.unlink(tmpScript).catch(() => {});
    }
  });

  it("maps spawn-failure (interpreter not found) to internal_error", async () => {
    process.env.MURMUR_PY = "/no/such/interpreter/exists/anywhere";
    const result = await defaultInvoker("probe_monitor", {}, "claim-x");
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual(["internal_error"]);
  });
});
