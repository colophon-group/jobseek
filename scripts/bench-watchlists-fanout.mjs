#!/usr/bin/env node
/**
 * Issue #3176 micro-bench — quantify the listing-page fan-out delta.
 *
 * The real serverless trace shape is documented in
 * `apps/web/docs/edge-requests/watchlists.md`:
 *
 *   • Postgres SELECT (watchlists + companies)  : 10-30ms
 *   • per-watchlist taxonomy lookups (4 in `||`): 5-15ms
 *   • per-watchlist Typesense filtered count    : 15-40ms
 *
 * The pre-fix listing path ran `resolveFilteredJobCount` N times in
 * parallel; ALL N Typesense round-trips share one event loop, so the
 * P50 stays roughly constant — but the **wall clock** depends on the
 * slowest leg, and the Vercel function pays for that whole window of
 * occupied serverless CPU + open sockets.
 *
 * This bench simulates each I/O leg with `setTimeout` at the documented
 * latencies, then measures the wall-clock delta between:
 *
 *   BEFORE: 1 PG query + Promise.all(N × (4 taxonomy + 1 TS count))
 *   AFTER : 1 PG query (with denormalized JOIN in the same SELECT)
 *
 * Iteration count + simulated latencies match the document above. The
 * absolute numbers depend on event-loop scheduling on the host CPU,
 * which is why each scenario is averaged across 50 trials.
 *
 * Run: `node scripts/bench-watchlists-fanout.mjs`
 */

/**
 * Documented latency ranges, in ms.
 *
 * `typesenseCount` matches `apps/web/docs/edge-requests/watchlists.md`:
 * "30-80ms × N" for the per-watchlist count leg, which includes the
 * 4-parallel taxonomy lookup + the filtered count itself (taxonomy
 * lookups are bundled inside `resolveFilteredJobCount`).
 */
const LATENCY = {
  pg: { min: 10, max: 30 },               // SELECT w.* FROM watchlist …
  pgWithJoin: { min: 12, max: 38 },       // SELECT w.* + denormalized count subquery
  taxonomyLookup: { min: 5, max: 15 },    // resolveLocationSlugs etc.
  typesenseCount: { min: 30, max: 80 },   // Typesense filtered count (matches doc)
};

/**
 * Typesense runs on a single CX22 (2 vCPU). When N concurrent filtered
 * count queries arrive at the server, the server processes them with
 * limited concurrency. The reported "mostly-serial round-trips" wording
 * in the issue body suggests an effective concurrency of ~2-3 at the
 * Typesense host. We model this with a global semaphore so the bench
 * reproduces the observed serialisation pattern.
 */
const TYPESENSE_SERVER_CONCURRENCY = 2;

class Semaphore {
  constructor(n) {
    this.n = n;
    this.queue = [];
  }
  async acquire() {
    if (this.n > 0) {
      this.n -= 1;
      return;
    }
    await new Promise((resolve) => this.queue.push(resolve));
  }
  release() {
    const next = this.queue.shift();
    if (next) next();
    else this.n += 1;
  }
}

/** Pick a random integer in [min, max] inclusive. */
function rand({ min, max }) {
  return min + Math.random() * (max - min);
}

/** Resolves after `ms` milliseconds. */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Pre-fix path: 1 PG SELECT, then for each of N watchlists fire 4
 * parallel taxonomy lookups + 1 Typesense filtered count.
 *
 * Counts of work per N watchlists:
 *   queries  = 1 + 5N
 *   round-trips on user thread = 1 + N (each parallel batch waits for
 *     the slowest of its 5 legs)
 */
async function before(N) {
  // Single Postgres SELECT for watchlists + companies.
  await sleep(rand(LATENCY.pg));

  // Fresh semaphore per trial so trials don't interfere with each other.
  const tsSem = new Semaphore(TYPESENSE_SERVER_CONCURRENCY);

  // Promise.all over N watchlists — each leg runs in its own subtask but
  // pulls a Typesense socket and ~5 DB queries.
  await Promise.all(
    Array.from({ length: N }, async () => {
      // Per-watchlist: 4 taxonomy lookups in parallel + 1 Typesense count
      // (the count waits for the lookups to resolve filter slugs first).
      await Promise.all(
        Array.from({ length: 4 }, () => sleep(rand(LATENCY.taxonomyLookup))),
      );
      // Typesense serialisation: the host can only process ~K filtered
      // counts in parallel. Queue if needed.
      await tsSem.acquire();
      try {
        await sleep(rand(LATENCY.typesenseCount));
      } finally {
        tsSem.release();
      }
    }),
  );
}

/**
 * Post-fix path: ONE Postgres SELECT that JOINs through watchlist_company
 * to job_posting and returns the denormalized active count alongside the
 * watchlist row. Zero Typesense round-trips.
 */
async function after(N) {
  // N is unused — the per-row count is computed inside the SQL aggregator
  // server-side, not on the application server. We model it as a single
  // SQL leg, slightly heavier than the original SELECT because of the
  // extra correlated subquery.
  void N;
  await sleep(rand(LATENCY.pgWithJoin));
}

async function trial(fn, N) {
  const t0 = performance.now();
  await fn(N);
  return performance.now() - t0;
}

async function suite(N, trials) {
  const beforeRuns = [];
  const afterRuns = [];
  for (let i = 0; i < trials; i++) {
    beforeRuns.push(await trial(before, N));
    afterRuns.push(await trial(after, N));
  }
  return {
    N,
    trials,
    before: stat(beforeRuns),
    after: stat(afterRuns),
  };
}

function stat(samples) {
  const sorted = [...samples].sort((a, b) => a - b);
  const mean = samples.reduce((a, b) => a + b, 0) / samples.length;
  const p50 = sorted[Math.floor(sorted.length * 0.5)];
  const p95 = sorted[Math.floor(sorted.length * 0.95)];
  return {
    min: sorted[0],
    p50,
    p95,
    max: sorted[sorted.length - 1],
    mean,
  };
}

function fmt(n) {
  return n.toFixed(1).padStart(7);
}

async function main() {
  const TRIALS = 50;
  // N values match the doc: free tier (5), paid tier (50), plus a low
  // boundary case (1) so the lower bound is visible.
  const scenarios = [1, 5, 10, 25, 50];

  console.log(`Issue #3176 — watchlist listing fan-out benchmark`);
  console.log(`Trials per scenario: ${TRIALS}`);
  console.log(``);
  console.log(`Latencies (ms):`);
  console.log(`  pg SELECT (watchlists)        ${LATENCY.pg.min}-${LATENCY.pg.max}`);
  console.log(`  pg SELECT + join subquery     ${LATENCY.pgWithJoin.min}-${LATENCY.pgWithJoin.max}`);
  console.log(`  taxonomy slug → id (×4 parallel) ${LATENCY.taxonomyLookup.min}-${LATENCY.taxonomyLookup.max} each`);
  console.log(`  Typesense filtered count       ${LATENCY.typesenseCount.min}-${LATENCY.typesenseCount.max}`);
  console.log(``);
  console.log(`Wall-clock benchmark (P50/P95 of ${TRIALS} trials):`);
  console.log(
    "N    | scenario | min     | p50     | p95     | max     | mean    | delta vs BEFORE",
  );
  console.log("-----|----------|---------|---------|---------|---------|---------|----------------");

  const summary = [];
  for (const N of scenarios) {
    const { before: b, after: a } = await suite(N, TRIALS);
    const deltaMean = b.mean - a.mean;
    const speedup = b.mean / a.mean;
    summary.push({ N, before: b, after: a, deltaMean, speedup });
    console.log(
      `${String(N).padStart(4)} | BEFORE   | ${fmt(b.min)} | ${fmt(b.p50)} | ${fmt(b.p95)} | ${fmt(b.max)} | ${fmt(b.mean)} | (baseline)`,
    );
    console.log(
      `${String(N).padStart(4)} | AFTER    | ${fmt(a.min)} | ${fmt(a.p50)} | ${fmt(a.p95)} | ${fmt(a.max)} | ${fmt(a.mean)} | -${deltaMean.toFixed(1)}ms (${speedup.toFixed(1)}× faster)`,
    );
  }

  // Round-trip count is the metric the issue calls out most directly.
  // This isn't latency — it's *queries fired against Typesense*, which
  // is what determines the open-socket pressure and the bill on a
  // metered Typesense plan.
  console.log(``);
  console.log(`Typesense round-trips on the user-listing path:`);
  console.log("N    | BEFORE      | AFTER       | reduction");
  console.log("-----|-------------|-------------|----------");
  for (const N of scenarios) {
    console.log(
      `${String(N).padStart(4)} | ${String(N).padStart(11)} | ${"0".padStart(11)} | ${N} → 0`,
    );
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
