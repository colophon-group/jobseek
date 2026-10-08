import { AsyncLocalStorage } from "node:async_hooks";
import { performance } from "node:perf_hooks";

export type SearchStage =
  | "rate_limit" | "parse_filters" | "resolve_filters" | "interpret_terms"
  | "session" | "provider" | "main_search" | "year_counts" | "company_metadata"
  | "active_counts" | "posting_hydration"
  | "location_lookup" | "occupation_lookup" | "seniority_lookup" | "technology_lookup";

type StageStats = {
  stage: SearchStage;
  calls: number;
  wall_ms: number;
  wall_max_ms: number;
  sdk_calls: number;
  sdk_errors: number;
  sdk_wall_ms: number;
  engine_samples: number;
  engine_ms: number;
  engine_max_ms: number;
  application_retries: number;
};
type Measurement = { closed: boolean; started: number; stages: Map<SearchStage, StageStats> };
type Scope = { measurement: Measurement; stage?: SearchStage };
const scope = new AsyncLocalStorage<Scope>();
const bound = (n: number) => Number.isFinite(n) ? Math.min(300_000, Math.max(0, Math.round(n))) : 0;

function stats(current: Scope) {
  const stage = current.stage;
  if (!stage || current.measurement.closed) return;
  let value = current.measurement.stages.get(stage);
  if (!value) {
    value = { stage, calls: 0, wall_ms: 0, wall_max_ms: 0, sdk_calls: 0, sdk_errors: 0,
      sdk_wall_ms: 0, engine_samples: 0, engine_ms: 0, engine_max_ms: 0,
      application_retries: 0 };
    current.measurement.stages.set(stage, value);
  }
  return value;
}

/** Only public REST search creates a scope. No query, header or result content enters it. */
export function createSearchMeasurement() {
  const measurement: Measurement = { closed: false, started: performance.now(), stages: new Map() };
  return {
    run<T>(operation: () => Promise<T>): Promise<T> {
      return scope.run({ measurement }, operation);
    },
    finish() {
      measurement.closed = true;
      return { schema_version: 1, sdk_scope: "request_context_only", sdk_internal_retries: "unobserved", duration_ms: bound(performance.now() - measurement.started),
        stages: [...measurement.stages.values()].map((value) => ({ ...value })) };
    },
  };
}

/** Wall times overlap for nested/parallel stages; they must not be added together. */
export async function measureSearchStage<T>(stage: SearchStage, operation: () => Promise<T>): Promise<T> {
  const parent = scope.getStore();
  if (!parent || parent.measurement.closed) return operation();
  const current = { measurement: parent.measurement, stage };
  const started = performance.now();
  try { return await scope.run(current, operation); }
  finally {
    const value = stats(current);
    if (value) {
      const elapsed = performance.now() - started;
      value.calls += 1;
      value.wall_ms = bound(value.wall_ms + elapsed);
      value.wall_max_ms = Math.max(value.wall_max_ms, bound(elapsed));
    }
  }
}

export function beginTypesenseMeasurement() {
  const current = scope.getStore();
  if (!current || current.measurement.closed || !current.stage) return undefined;
  const started = performance.now();
  let finished = false;
  return (result?: unknown, failed = false) => {
    if (finished) return;
    finished = true;
    const value = stats(current);
    if (!value) return;
    value.sdk_calls += 1;
    value.sdk_errors += Number(failed);
    value.sdk_wall_ms = bound(value.sdk_wall_ms + performance.now() - started);
    // SDK wall includes transport, queueing, body parsing and internal retries.
    // search_time_ms reports only the engine's returned timing, not network time.
    try {
      const responses = result && typeof result === "object" && Array.isArray(Reflect.get(result, "results"))
        ? Reflect.get(result, "results") as unknown[] : [result];
      for (const response of responses.slice(0, 1000)) {
        if (!response || typeof response !== "object") continue;
        const ms: unknown = Reflect.get(response, "search_time_ms");
        if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) continue;
        value.engine_samples += 1;
        value.engine_ms = bound(value.engine_ms + ms);
        value.engine_max_ms = Math.max(value.engine_max_ms, bound(ms));
      }
    } catch { /* Reading telemetry must never affect SDK results. */ }
  };
}

export function recordSearchRetry() {
  const current = scope.getStore();
  const value = current && stats(current);
  if (value) value.application_retries += 1;
}
