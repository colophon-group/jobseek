/**
 * Unit tests for `defaultRerunProbes` — the per-board fan-out + race +
 * error aggregation in `apps/web/app/api/murmur/_lib/accept-pipeline.ts`.
 *
 * Background:
 *   `accept.test.ts` stubs `RerunHolder.current` outright, so the real
 *   `defaultRerunProbes` (the Promise.race / per-board fan-out / error
 *   aggregation) never runs in that suite. This file fills the gap by
 *   driving the real implementation against a stubbed `InvokerHolder`.
 *
 * @see colophon-group/jobseek#2763
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import { defaultRerunProbes } from "../_lib/accept-pipeline";
import { InvokerHolder, type LibInvoker } from "../_lib/invoke-lib";
import type { FinalOutput } from "../_lib/accept-schema";

const TWO_BOARD_BODY: FinalOutput = {
  canonical_name: "Acme",
  canonical_website: "https://acme.example.com",
  slug: "acme",
  description: "Acme.",
  industry_ids: ["software"],
  boards: [
    {
      alias: "global",
      board_url: "https://job-boards.greenhouse.io/acme",
      provider: "greenhouse",
      outcome: "configured",
      monitor_type: "greenhouse",
      monitor_config: { token: "acme" },
      scraper_type: "skip",
      scraper_config: {},
      verdict: "ok",
    },
    {
      alias: "eu",
      board_url: "https://jobs.lever.co/acme-eu",
      provider: "lever",
      outcome: "configured",
      monitor_type: "lever",
      monitor_config: { site: "acme-eu" },
      scraper_type: "skip",
      scraper_config: {},
      verdict: "ok",
    },
  ],
};

let originalInvoker: LibInvoker;
let originalTimeout: string | undefined;

beforeEach(() => {
  originalInvoker = InvokerHolder.current;
  originalTimeout = process.env.MURMUR_ACCEPT_PROBE_TIMEOUT_MS;
});

afterEach(() => {
  InvokerHolder.current = originalInvoker;
  if (originalTimeout === undefined) {
    delete process.env.MURMUR_ACCEPT_PROBE_TIMEOUT_MS;
  } else {
    process.env.MURMUR_ACCEPT_PROBE_TIMEOUT_MS = originalTimeout;
  }
});

describe("defaultRerunProbes", () => {
  it("returns ok when every board's invokeLib resolves ok:true", async () => {
    const seen: string[] = [];
    InvokerHolder.current = (async (subcommand, body) => {
      expect(subcommand).toBe("probe_monitor");
      seen.push((body as { board_url: string }).board_url);
      return { ok: true, data: {} };
    }) as LibInvoker;

    const result = await defaultRerunProbes(TWO_BOARD_BODY);
    expect(result).toEqual({ status: "ok" });
    // Each board should have been probed exactly once, in parallel.
    expect(seen.sort()).toEqual([
      "https://job-boards.greenhouse.io/acme",
      "https://jobs.lever.co/acme-eu",
    ]);
  });

  it("aggregates per-board failures with `<token>:<alias>` formatting", async () => {
    InvokerHolder.current = (async (_sub, body) => {
      const url = (body as { board_url: string }).board_url;
      if (url.includes("greenhouse")) {
        return { ok: false, errors: ["probe_failed"] };
      }
      return { ok: false, errors: ["timeout", "internal_error"] };
    }) as LibInvoker;

    const result = await defaultRerunProbes(TWO_BOARD_BODY);
    expect(result.status).toBe("failed");
    if (result.status !== "failed") return; // narrow for TS
    // Two boards; greenhouse contributes 1 error, lever contributes 2.
    expect([...result.errors].sort()).toEqual(
      [
        "internal_error:eu",
        "probe_failed:global",
        "timeout:eu",
      ].sort(),
    );
  });

  it("falls back to `probe_failed:<alias>` when the lib returns no errors array", async () => {
    InvokerHolder.current = (async () => ({ ok: false })) as LibInvoker;

    const result = await defaultRerunProbes(TWO_BOARD_BODY);
    expect(result.status).toBe("failed");
    if (result.status !== "failed") return;
    expect([...result.errors].sort()).toEqual(
      ["probe_failed:eu", "probe_failed:global"].sort(),
    );
  });

  it("reports timeout when the budget elapses before the fan-out completes", async () => {
    process.env.MURMUR_ACCEPT_PROBE_TIMEOUT_MS = "5";
    InvokerHolder.current = (async () => {
      // Sleep longer than the budget so the racing promise loses.
      await new Promise((r) => setTimeout(r, 100));
      return { ok: true };
    }) as LibInvoker;

    const result = await defaultRerunProbes(TWO_BOARD_BODY);
    expect(result).toEqual({ status: "timeout" });
  });

  it("ignores invalid MURMUR_ACCEPT_PROBE_TIMEOUT_MS and falls back to default", async () => {
    process.env.MURMUR_ACCEPT_PROBE_TIMEOUT_MS = "not-a-number";
    const warnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);

    InvokerHolder.current = (async () => ({ ok: true })) as LibInvoker;

    const result = await defaultRerunProbes(TWO_BOARD_BODY);
    expect(result).toEqual({ status: "ok" });
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it("ignores zero / negative MURMUR_ACCEPT_PROBE_TIMEOUT_MS and falls back to default", async () => {
    process.env.MURMUR_ACCEPT_PROBE_TIMEOUT_MS = "0";
    const warnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);

    InvokerHolder.current = (async () => ({ ok: true })) as LibInvoker;
    const result = await defaultRerunProbes(TWO_BOARD_BODY);
    expect(result).toEqual({ status: "ok" });
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});
