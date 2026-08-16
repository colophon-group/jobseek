import { describe, expect, it } from "vitest";
import { analyzeVercelLogs } from "../../../script/analyze-vercel-logs";

describe("analyzeVercelLogs", () => {
  it("filters exact paths and reports request concentrations", () => {
    const actionId = "7f4012f212256c22a4774bd1ff905c747cbd90798";
    const summary = analyzeVercelLogs(
      [
        {
          timestamp: 0,
          requestMethod: "POST",
          requestPath: "/en/explore",
          responseStatusCode: 200,
          cache: "BYPASS",
          cacheReason: "prerender_bypass",
          source: "serverless",
          level: "info",
          message: "",
        },
        {
          timestamp: 60_000,
          requestMethod: "POST",
          requestPath: "/en/explore",
          responseStatusCode: 404,
          cache: "",
          cacheReason: "",
          source: "static",
          level: "warning",
          message: `Failed to find Server Action "${actionId}"`,
        },
        {
          timestamp: 60_000,
          requestMethod: "GET",
          requestPath: "/en/about",
          responseStatusCode: 200,
          source: "static",
          level: "info",
          message: "",
        },
      ],
      { paths: ["/en/explore"] },
    );

    expect(summary.input_entries).toBe(3);
    expect(summary.matched_entries).toBe(2);
    expect(summary.filtered_out_entries).toBe(1);
    expect(summary.window).toEqual({
      first_at: "1970-01-01T00:00:00.000Z",
      last_at: "1970-01-01T00:01:00.000Z",
      span_seconds: 60,
    });
    expect(summary.log_entries_per_minute).toBe(2);
    expect(summary.concentrations.request_method).toEqual([
      { value: "POST", count: 2 },
    ]);
    expect(summary.concentrations.response_status).toEqual([
      { value: 200, count: 1 },
      { value: 404, count: 1 },
    ]);
    expect(summary.concentrations.cache).toEqual([
      {
        value: { result: "(none)", reason: "(none)" },
        count: 1,
      },
      {
        value: { result: "BYPASS", reason: "prerender_bypass" },
        count: 1,
      },
    ]);
    expect(summary.concentrations.server_action_id).toEqual([
      { value: actionId, count: 1 },
    ]);
  });

  it("returns an explicit empty summary when no path matches", () => {
    const summary = analyzeVercelLogs(
      [
        {
          timestamp: 10,
          requestMethod: "GET",
          requestPath: "/en/about",
        },
      ],
      { paths: ["/en/explore"] },
    );

    expect(summary.matched_entries).toBe(0);
    expect(summary.filtered_out_entries).toBe(1);
    expect(summary.window).toEqual({
      first_at: null,
      last_at: null,
      span_seconds: null,
    });
    expect(summary.log_entries_per_minute).toBeNull();
    expect(summary.concentrations.request_path).toEqual([]);
  });
});
