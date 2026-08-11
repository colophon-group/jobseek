/** Evaluate a clean post-deployment Vercel Fluid CPU observation window. */
import { readFileSync } from "node:fs";

export const FLUID_CPU_BASELINE = {
  windowHours: 12,
  visibleActiveCpuSeconds: 277,
  activeCpuP75Ms: 308,
  cpuThrottleP75Pct: 7.6,
  invocations: 1_500,
  routes: {
    companyOg: 120,
    companyPages: 91,
    publicWatchlists: 39,
    explore: 10,
    other: 17,
  },
} as const;

export const FLUID_CPU_BUDGET = {
  minimumWindowHours: 12,
  visibleActiveCpuSeconds: 138.5,
  activeCpuP75Ms: 308,
  cpuThrottleP75Pct: 7.6,
  errorRatePct: 0.5,
  timeoutRatePct: 0.1,
  typesenseCallsPerInvocation: 2.5,
  upstashCallsPerInvocation: 1.5,
  companyOgR2HitRatePct: 95,
  pprShellHitRatePct: 35,
  minimumRecognizedBotRequests: 1,
  minimumLongTailUniqueKeys: 20,
  routes: {
    companyOg: 24,
    companyPages: 60,
    publicWatchlists: 25,
    explore: 10,
    other: 19.5,
  },
} as const;

const REQUIRED_ROUTES = [
  "companyOg",
  "companyPages",
  "publicWatchlists",
  "explore",
  "other",
] as const;

const REQUIRED_FUNCTIONALITY = [
  "home",
  "explore",
  "companyPage",
  "companyOg",
  "publicWatchlist",
  "authentication",
] as const;

type Check = {
  name: string;
  actual: string;
  budget: string;
  passed: boolean;
};

export type FluidCpuGateResult = {
  checks: Check[];
  markdown: string;
  passed: boolean;
};

function record(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function finiteNumber(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error(`${name} must be a non-negative finite number`);
  }
  return value;
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function requiredBoolean(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${name} must be a boolean`);
  return value;
}

function timestamp(value: unknown, name: string): number {
  const raw = requiredString(value, name);
  const parsed = Date.parse(raw);
  if (!Number.isFinite(parsed)) throw new Error(`${name} must be an ISO timestamp`);
  return parsed;
}

function format(value: number, digits = 1): string {
  return value.toFixed(digits).replace(/\.0$/, "");
}

function ratioPercent(numerator: number, denominator: number): number {
  return denominator === 0 ? 0 : (numerator / denominator) * 100;
}

function addMaximum(
  checks: Check[],
  name: string,
  actual: number,
  budget: number,
  unit: string,
) {
  checks.push({
    name,
    actual: `${format(actual)}${unit}`,
    budget: `≤ ${format(budget)}${unit}`,
    passed: actual <= budget,
  });
}

function addMinimum(
  checks: Check[],
  name: string,
  actual: number,
  budget: number,
  unit: string,
) {
  checks.push({
    name,
    actual: `${format(actual)}${unit}`,
    budget: `≥ ${format(budget)}${unit}`,
    passed: actual >= budget,
  });
}

export function evaluateFluidCpuReport(input: unknown): FluidCpuGateResult {
  const report = record(input, "report");
  if (report.schemaVersion !== 1) throw new Error("schemaVersion must be 1");

  const deployment = record(report.deployment, "deployment");
  const sha = requiredString(deployment.sha, "deployment.sha");
  if (!/^[a-f0-9]{7,40}$/i.test(sha)) throw new Error("deployment.sha is invalid");
  const readyAt = timestamp(deployment.readyAt, "deployment.readyAt");

  const window = record(report.window, "window");
  const windowStart = timestamp(window.start, "window.start");
  const windowEnd = timestamp(window.end, "window.end");
  if (windowEnd <= windowStart) throw new Error("window.end must be after window.start");
  const windowHours = (windowEnd - windowStart) / 3_600_000;

  const totals = record(report.totals, "totals");
  const invocations = finiteNumber(totals.invocations, "totals.invocations");
  if (invocations < 1) throw new Error("totals.invocations must be at least 1");
  const visibleCpu = finiteNumber(
    totals.visibleActiveCpuSeconds,
    "totals.visibleActiveCpuSeconds",
  );
  const activeCpuP75 = finiteNumber(totals.activeCpuP75Ms, "totals.activeCpuP75Ms");
  const throttleP75 = finiteNumber(
    totals.cpuThrottleP75Pct,
    "totals.cpuThrottleP75Pct",
  );
  const errorRate = finiteNumber(totals.errorRatePct, "totals.errorRatePct");
  const timeoutRate = finiteNumber(totals.timeoutRatePct, "totals.timeoutRatePct");
  const typesenseCalls = finiteNumber(totals.typesenseCalls, "totals.typesenseCalls");
  const upstashCalls = finiteNumber(totals.upstashCalls, "totals.upstashCalls");

  const routes = record(report.routes, "routes");
  const routeCpu = {} as Record<(typeof REQUIRED_ROUTES)[number], number>;
  for (const routeName of REQUIRED_ROUTES) {
    const metric = record(routes[routeName], `routes.${routeName}`);
    finiteNumber(metric.invocations, `routes.${routeName}.invocations`);
    routeCpu[routeName] = finiteNumber(
      metric.activeCpuSeconds,
      `routes.${routeName}.activeCpuSeconds`,
    );
  }

  const cache = record(report.cache, "cache");
  const companyOgR2Hits = finiteNumber(cache.companyOgR2Hits, "cache.companyOgR2Hits");
  const companyOgR2Misses = finiteNumber(
    cache.companyOgR2Misses,
    "cache.companyOgR2Misses",
  );
  const pprShellHits = finiteNumber(cache.pprShellHits, "cache.pprShellHits");
  const pprShellMisses = finiteNumber(cache.pprShellMisses, "cache.pprShellMisses");

  const traffic = record(report.traffic, "traffic");
  const recognizedBotRequests = finiteNumber(
    traffic.recognizedBotRequests,
    "traffic.recognizedBotRequests",
  );
  const longTailUniqueKeys = finiteNumber(
    traffic.longTailUniqueKeys,
    "traffic.longTailUniqueKeys",
  );
  const sourceIncludesAllTraffic = requiredBoolean(
    traffic.sourceIncludesAllTraffic,
    "traffic.sourceIncludesAllTraffic",
  );

  const functionality = record(report.functionality, "functionality");
  const functionalityChecks = REQUIRED_FUNCTIONALITY.map((name) => ({
    name,
    passed: requiredBoolean(functionality[name], `functionality.${name}`),
  }));

  const checks: Check[] = [];
  checks.push({
    name: "Window starts after deployment ready",
    actual: new Date(windowStart).toISOString(),
    budget: `≥ ${new Date(readyAt).toISOString()}`,
    passed: windowStart >= readyAt,
  });
  addMinimum(checks, "Clean window duration", windowHours, FLUID_CPU_BUDGET.minimumWindowHours, "h");
  addMaximum(checks, "Visible Active CPU", visibleCpu, FLUID_CPU_BUDGET.visibleActiveCpuSeconds, "s");
  addMaximum(checks, "Active CPU P75", activeCpuP75, FLUID_CPU_BUDGET.activeCpuP75Ms, "ms");
  addMaximum(checks, "CPU throttle P75", throttleP75, FLUID_CPU_BUDGET.cpuThrottleP75Pct, "%");
  addMaximum(checks, "Error rate", errorRate, FLUID_CPU_BUDGET.errorRatePct, "%");
  addMaximum(checks, "Timeout rate", timeoutRate, FLUID_CPU_BUDGET.timeoutRatePct, "%");
  addMaximum(
    checks,
    "Typesense calls / invocation",
    typesenseCalls / invocations,
    FLUID_CPU_BUDGET.typesenseCallsPerInvocation,
    "",
  );
  addMaximum(
    checks,
    "Upstash calls / invocation",
    upstashCalls / invocations,
    FLUID_CPU_BUDGET.upstashCallsPerInvocation,
    "",
  );

  for (const routeName of REQUIRED_ROUTES) {
    addMaximum(
      checks,
      `${routeName} Active CPU`,
      routeCpu[routeName],
      FLUID_CPU_BUDGET.routes[routeName],
      "s",
    );
  }

  const routeCpuSum = Object.values(routeCpu).reduce((sum, value) => sum + value, 0);
  checks.push({
    name: "Route CPU reconciliation",
    actual: `${format(Math.abs(routeCpuSum - visibleCpu))}s difference`,
    budget: "≤ 0.5s difference",
    passed: Math.abs(routeCpuSum - visibleCpu) <= 0.5,
  });

  addMinimum(
    checks,
    "Company OG R2 hit rate",
    ratioPercent(companyOgR2Hits, companyOgR2Hits + companyOgR2Misses),
    FLUID_CPU_BUDGET.companyOgR2HitRatePct,
    "%",
  );
  addMinimum(
    checks,
    "PPR shell hit rate",
    ratioPercent(pprShellHits, pprShellHits + pprShellMisses),
    FLUID_CPU_BUDGET.pprShellHitRatePct,
    "%",
  );
  addMinimum(
    checks,
    "Recognized bot requests represented",
    recognizedBotRequests,
    FLUID_CPU_BUDGET.minimumRecognizedBotRequests,
    "",
  );
  addMinimum(
    checks,
    "Long-tail unique keys represented",
    longTailUniqueKeys,
    FLUID_CPU_BUDGET.minimumLongTailUniqueKeys,
    "",
  );
  checks.push({
    name: "Traffic source includes all requests",
    actual: String(sourceIncludesAllTraffic),
    budget: "true",
    passed: sourceIncludesAllTraffic,
  });

  for (const functionalityCheck of functionalityChecks) {
    checks.push({
      name: `Functionality: ${functionalityCheck.name}`,
      actual: String(functionalityCheck.passed),
      budget: "true",
      passed: functionalityCheck.passed,
    });
  }

  const passed = checks.every((check) => check.passed);
  const markdown = [
    `# Vercel Fluid CPU gate: ${passed ? "PASS" : "FAIL"}`,
    "",
    `Deployment: \`${sha}\``,
    `Window: ${new Date(windowStart).toISOString()} → ${new Date(windowEnd).toISOString()} (${format(windowHours, 2)}h)`,
    "",
    "| Check | Actual | Budget | Result |",
    "|---|---:|---:|:---:|",
    ...checks.map((check) =>
      `| ${check.name} | ${check.actual} | ${check.budget} | ${check.passed ? "PASS" : "FAIL"} |`,
    ),
    "",
    `Visible CPU reduction vs baseline: ${format((1 - visibleCpu / FLUID_CPU_BASELINE.visibleActiveCpuSeconds) * 100)}%`,
  ].join("\n");

  return { checks, markdown, passed };
}

function parseArguments(argv: string[]): { inputPath: string | null } {
  let inputPath: string | null = null;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--") continue;
    if (argument === "--input") {
      inputPath = argv[++index] ?? null;
      if (!inputPath) throw new Error("--input requires a path");
    } else {
      throw new Error(`Unknown argument: ${argument}`);
    }
  }
  return { inputPath };
}

function main() {
  const { inputPath } = parseArguments(process.argv.slice(2));
  const raw = inputPath
    ? readFileSync(inputPath, "utf8")
    : process.env.FLUID_CPU_REPORT_JSON;
  if (!raw) throw new Error("Provide --input or FLUID_CPU_REPORT_JSON");
  const result = evaluateFluidCpuReport(JSON.parse(raw));
  console.log(result.markdown);
  if (!result.passed) process.exitCode = 1;
}

const entrypoint = process.argv[1] ?? "";
if (/check-fluid-cpu-budget\.(?:ts|js|mjs|cjs)$/.test(entrypoint)) {
  try {
    main();
  } catch {
    console.error("fluid_cpu_budget_report_invalid");
    process.exitCode = 1;
  }
}
