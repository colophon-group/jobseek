/**
 * Observability tests for `logIndexNowResult` (#3202).
 *
 * The 5 web-side call sites in `apps/web/src/lib/services/watchlists.ts`
 * used to discard the `NotifyIndexNowResult` envelope, leaving operators
 * blind to rejection storms (e.g. a stale INDEXNOW_KEY cached on Bing
 * for 24h after a rotation, with Yandex/Seznam returning 422/403). The
 * helper plumbs every outcome to a single structured log line so the
 * rejection rate is filterable in Vercel logs.
 *
 * These tests don't exercise `notifyIndexNow` itself — that surface is
 * covered by `indexnow.test.ts`. We only assert the log-shape contract
 * the helper produces from each envelope shape.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  logIndexNowResult,
  INDEXNOW_LOG_EVENT,
  type NotifyIndexNowResult,
} from "../indexnow";

let infoSpy: ReturnType<typeof vi.spyOn>;
let warnSpy: ReturnType<typeof vi.spyOn>;
let debugSpy: ReturnType<typeof vi.spyOn>;
let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("logIndexNowResult", () => {
  it("emits a console.info line with the stable event name for submitted results", () => {
    const result: NotifyIndexNowResult = {
      kind: "submitted",
      status: 200,
      urlCount: 4,
    };
    logIndexNowResult("createWatchlist", result);

    expect(infoSpy).toHaveBeenCalledOnce();
    expect(warnSpy).not.toHaveBeenCalled();
    expect(debugSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();

    const [event, payload] = infoSpy.mock.calls[0];
    expect(event).toBe(INDEXNOW_LOG_EVENT);
    expect(event).toBe("indexnow.result");
    expect(payload).toMatchObject({
      label: "createWatchlist",
      kind: "submitted",
      status: 200,
      urlCount: 4,
      host: "api.indexnow.org",
    });
  });

  it("emits console.warn with status + host for rejected results (the actionable case)", () => {
    const result: NotifyIndexNowResult = {
      kind: "rejected",
      status: 422,
      urlCount: 8,
    };
    logIndexNowResult("updateWatchlist", result);

    expect(warnSpy).toHaveBeenCalledOnce();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(debugSpy).not.toHaveBeenCalled();

    const [event, payload] = warnSpy.mock.calls[0];
    expect(event).toBe(INDEXNOW_LOG_EVENT);
    expect(payload).toMatchObject({
      label: "updateWatchlist",
      kind: "rejected",
      status: 422,
      urlCount: 8,
      host: "api.indexnow.org",
    });
  });

  it("emits console.warn with the error message (not the raw object) for errored results", () => {
    const networkErr = new Error("ECONNRESET");
    const result: NotifyIndexNowResult = {
      kind: "errored",
      error: networkErr,
      urlCount: 4,
    };
    logIndexNowResult("deleteWatchlist", result);

    expect(warnSpy).toHaveBeenCalledOnce();
    const [event, payload] = warnSpy.mock.calls[0];
    expect(event).toBe(INDEXNOW_LOG_EVENT);
    expect(payload).toMatchObject({
      label: "deleteWatchlist",
      kind: "errored",
      error: "ECONNRESET",
      urlCount: 4,
    });
  });

  it("stringifies non-Error errored values defensively", () => {
    // `notifyIndexNow` types `error` as `unknown`. A non-Error throw
    // (string, number, AbortSignal timeout's DOMException-ish object)
    // must still serialise cleanly to a log line.
    const result: NotifyIndexNowResult = {
      kind: "errored",
      error: "raw string thrown",
      urlCount: 4,
    };
    logIndexNowResult("copyWatchlist", result);

    expect(warnSpy).toHaveBeenCalledOnce();
    const payload = warnSpy.mock.calls[0][1] as { error: string };
    expect(payload.error).toBe("raw string thrown");
  });

  it("emits console.debug (minimal output) for skipped results — preview deploys without INDEXNOW_KEY", () => {
    const result: NotifyIndexNowResult = {
      kind: "skipped",
      reason: "no-key",
    };
    logIndexNowResult("createWatchlist", result);

    expect(debugSpy).toHaveBeenCalledOnce();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();

    const [event, payload] = debugSpy.mock.calls[0];
    expect(event).toBe(INDEXNOW_LOG_EVENT);
    expect(payload).toMatchObject({
      label: "createWatchlist",
      kind: "skipped",
      reason: "no-key",
      urlCount: 0,
    });
  });

  it("carries the call-site label through verbatim so logs are attributable", () => {
    // Each of the 5 watchlist call sites passes a unique label; the
    // helper must never rewrite it. This guards against an accidental
    // refactor that collapses labels (e.g. via a default arg).
    const labels = [
      "createWatchlist",
      "updateWatchlist",
      "updateWatchlist:unpublish",
      "deleteWatchlist",
      "copyWatchlist",
    ];
    for (const label of labels) {
      logIndexNowResult(label, {
        kind: "submitted",
        status: 200,
        urlCount: 1,
      });
    }
    expect(infoSpy).toHaveBeenCalledTimes(labels.length);
    for (let i = 0; i < labels.length; i++) {
      expect(infoSpy.mock.calls[i][1]).toMatchObject({ label: labels[i] });
    }
  });

  it("uses the same stable event name for every outcome (one Vercel filter catches them all)", () => {
    const envelopes: NotifyIndexNowResult[] = [
      { kind: "submitted", status: 200, urlCount: 4 },
      { kind: "skipped", reason: "no-key" },
      { kind: "rejected", status: 403, urlCount: 4 },
      { kind: "errored", error: new Error("boom"), urlCount: 4 },
    ];
    for (const r of envelopes) logIndexNowResult("test", r);

    const allCalls = [
      ...infoSpy.mock.calls,
      ...debugSpy.mock.calls,
      ...warnSpy.mock.calls,
    ];
    expect(allCalls).toHaveLength(envelopes.length);
    for (const call of allCalls) {
      expect(call[0]).toBe("indexnow.result");
    }
  });
});
