import { describe, expect, it } from "vitest";
import {
  evaluateFluidCpuReport,
  FLUID_CPU_BUDGET,
} from "../../../script/check-fluid-cpu-budget";

function passingReport() {
  return {
    schemaVersion: 2,
    deployment: {
      sha: "1234567890abcdef",
      readyAt: "2026-08-11T00:00:00.000Z",
    },
    window: {
      start: "2026-08-11T00:05:00.000Z",
      end: "2026-08-11T12:05:00.000Z",
    },
    totals: {
      invocations: 1_000,
      visibleActiveCpuSeconds: 130,
      activeCpuP75Ms: 240,
      cpuThrottleP75Pct: 5,
      errorRatePct: 0,
      timeoutRatePct: 0,
      typesenseCalls: 2_000,
      upstashCalls: 1_000,
    },
    routes: {
      companyOg: { invocations: 200, activeCpuSeconds: 20 },
      companyPages: { invocations: 300, activeCpuSeconds: 58 },
      watchlistRoutes: { invocations: 150, activeCpuSeconds: 24 },
      explore: { invocations: 100, activeCpuSeconds: 9 },
      other: { invocations: 250, activeCpuSeconds: 19 },
    },
    cache: {
      pprShellHits: 400,
      pprShellMisses: 600,
    },
    traffic: {
      recognizedBotRequests: 250,
      longTailUniqueKeys: 300,
      sourceIncludesAllTraffic: true,
      source: "100% production Vercel log drain",
      samplingRatePct: 100,
    },
    approvals: {
      companyOgR2Revision: "https://github.com/colophon-group/jobseek/issues/10015#issuecomment-1",
    },
    functionality: {
      home: true,
      explore: true,
      companyPage: true,
      companyOgDirect: true,
      companyOgLegacyRedirect: true,
      ownedWatchlist: true,
      sharedWatchlist: true,
      privateWatchlistAnonymousDenied: true,
      privateWatchlistCrossOwnerDenied: true,
      legacyWatchlistAnonymousDenied: true,
      legacyWatchlistCrossOwnerDenied: true,
      legacyWatchlistOwnerRedirects: true,
      authentication: true,
    },
  };
}

describe("Fluid CPU regression gate", () => {
  it("passes a clean 12-hour report inside every budget", () => {
    const result = evaluateFluidCpuReport(passingReport());

    expect(result.passed).toBe(true);
    expect(result.markdown).toContain("Vercel Fluid CPU gate: PASS");
  });

  it("fails the 50% total CPU target even when route functionality passes", () => {
    const report = passingReport();
    report.totals.visibleActiveCpuSeconds = FLUID_CPU_BUDGET.visibleActiveCpuSeconds + 1;
    report.routes.other.activeCpuSeconds += 9.5;

    const result = evaluateFluidCpuReport(report);

    expect(result.passed).toBe(false);
    expect(result.checks.find((check) => check.name === "Visible Active CPU"))
      .toMatchObject({ passed: false });
  });

  it("rejects windows containing pre-deployment traffic", () => {
    const report = passingReport();
    report.window.start = "2026-08-10T23:59:00.000Z";

    const result = evaluateFluidCpuReport(report);

    expect(result.passed).toBe(false);
    expect(result.checks[0]).toMatchObject({
      name: "Window starts after deployment ready",
      passed: false,
    });
  });

  it("fails when a live production functionality check fails", () => {
    const report = passingReport();
    report.functionality.companyOgDirect = false;

    const result = evaluateFluidCpuReport(report);

    expect(result.passed).toBe(false);
    expect(result.checks.find((check) => check.name === "Functionality: companyOgDirect"))
      .toMatchObject({ passed: false });
  });

  it("fails hot-key-only samples without bot and long-tail representation", () => {
    const report = passingReport();
    report.traffic.recognizedBotRequests = 0;
    report.traffic.longTailUniqueKeys = 1;

    const result = evaluateFluidCpuReport(report);

    expect(result.passed).toBe(false);
    expect(result.checks.filter((check) => !check.passed).map((check) => check.name))
      .toEqual(expect.arrayContaining([
        "Recognized bot requests represented",
        "Long-tail unique keys represented",
      ]));
  });

  it("reports unavailable exact external counts as incomplete without replacing them with zero", () => {
    const report = passingReport();
    const incomplete = {
      ...report,
      totals: { ...report.totals, typesenseCalls: null, upstashCalls: null },
    };

    const result = evaluateFluidCpuReport(incomplete);

    expect(result.status).toBe("INCOMPLETE");
    expect(result.passed).toBe(false);
    expect(result.markdown).toContain("Typesense calls / invocation | unknown");
    expect(result.markdown).toContain("Upstash calls / invocation | unknown");
  });

  it("requires an approval reference before the revised R2 gate can pass", () => {
    const report = passingReport();
    const result = evaluateFluidCpuReport({
      ...report,
      approvals: { companyOgR2Revision: null },
    });

    expect(result.status).toBe("INCOMPLETE");
    expect(result.checks.find((check) => check.name === "Company OG R2 gate revision approved"))
      .toMatchObject({ available: false, passed: false });
  });

  it("rejects an approval reference outside the repository", () => {
    const report = passingReport();
    expect(() => evaluateFluidCpuReport({
      ...report,
      approvals: { companyOgR2Revision: "https://example.com/approved" },
    })).toThrow("approvals.companyOgR2Revision must link to a repository approval comment");
  });

  it("keeps measured CPU failure visible when other evidence is incomplete", () => {
    const report = passingReport();
    const result = evaluateFluidCpuReport({
      ...report,
      totals: {
        ...report.totals,
        visibleActiveCpuSeconds: 150,
        typesenseCalls: null,
      },
    });

    expect(result.status).toBe("FAIL (INCOMPLETE EVIDENCE)");
    expect(result.checks.find((check) => check.name === "Visible Active CPU"))
      .toMatchObject({ passed: false, available: true });
  });

  it("fails if private watchlist access checks or full traffic sampling fail", () => {
    const report = passingReport();
    report.functionality.privateWatchlistCrossOwnerDenied = false;
    report.traffic.samplingRatePct = 10;

    const result = evaluateFluidCpuReport(report);

    expect(result.status).toBe("FAIL");
    expect(result.checks.filter((check) => !check.passed).map((check) => check.name))
      .toEqual(expect.arrayContaining([
        "Functionality: privateWatchlistCrossOwnerDenied",
        "Traffic source sampling",
      ]));
  });
});
