import { describe, expect, it } from "vitest";
import { createSearchMeasurement, measureSearchStage, recordSearchRetry } from "../latency";
import { withTypesenseRetry } from "../typesense-retry";
import { sanitizeTypesenseClientBoundary } from "../typesense-client";

describe("search stage measurements", () => {
  it("isolates simultaneous requests and ignores late background work", async () => {
    const first = createSearchMeasurement();
    const second = createSearchMeasurement();
    let resolve!: () => void;
    const gate = new Promise<void>((done) => { resolve = done; });
    const work = first.run(() => measureSearchStage("main_search", async () => {
      await gate;
      recordSearchRetry();
    }));
    await second.run(() => measureSearchStage("year_counts", async () => recordSearchRetry()));
    expect(second.finish().stages).toEqual([expect.objectContaining({ stage: "year_counts", application_retries: 1 })]);
    const snapshot = first.finish();
    resolve();
    await work;
    expect(snapshot.stages).toEqual([]);
    expect(first.finish().stages).toEqual([]);
  });

  it("counts application retries without changing the retry policy", async () => {
    const measurement = createSearchMeasurement();
    let attempts = 0;
    const result = await measurement.run(() => measureSearchStage("main_search", () => withTypesenseRetry(async () => {
      attempts += 1;
      if (attempts === 1) throw { code: "ECONNRESET" };
      return "unchanged";
    }, { sleep: async () => undefined, maxJitterMs: 0 })));
    expect(result).toBe("unchanged");
    expect(attempts).toBe(2);
    expect(measurement.finish().stages[0]).toMatchObject({ stage: "main_search", application_retries: 1 });
  });

  it("records SDK wait and engine time without result or credential content", async () => {
    const measurement = createSearchMeasurement();
    const client = sanitizeTypesenseClientBoundary({ search: async () => ({
      search_time_ms: 13, hits: [{ document: { title: "PRIVATE_RESULT_CANARY" } }],
    }) });
    const broken = sanitizeTypesenseClientBoundary({ search: async () => {
      throw { message: "PRIVATE_ERROR_CANARY", config: { apiKey: "PRIVATE_KEY_CANARY" } };
    } });
    await measurement.run(() => measureSearchStage("main_search", async () => {
      expect((await client.search()).hits[0].document.title).toBe("PRIVATE_RESULT_CANARY");
      await expect(broken.search()).rejects.toThrow("Typesense request failed");
    }));
    const snapshot = measurement.finish();
    expect(snapshot.stages).toEqual([expect.objectContaining({ stage: "main_search", calls: 1,
      sdk_calls: 2, sdk_errors: 1, engine_samples: 1, engine_ms: 13, engine_max_ms: 13 })]);
    expect(JSON.stringify(snapshot)).not.toContain("PRIVATE_");
  });

  it("ignores hostile timing getters while preserving results and errors", async () => {
    const measurement = createSearchMeasurement();
    const result = Object.defineProperty({}, "search_time_ms", { get: () => { throw new Error("PRIVATE_CANARY"); } });
    const client = sanitizeTypesenseClientBoundary({ search: async () => result });
    await measurement.run(() => measureSearchStage("company_metadata", async () => {
      expect(await client.search()).toBe(result);
    }));
    expect(measurement.finish().stages[0]).toMatchObject({ sdk_calls: 1, engine_samples: 0 });
    const error = new Error("original");
    await expect(measureSearchStage("main_search", async () => { throw error; })).rejects.toBe(error);
  });
});
