import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTestEnv, withTestEnv } from "@/test-utils/env";
import { notifyIndexNow } from "../indexnow";

const fetchMock = vi.fn();

withTestEnv({ INDEXNOW_KEY: undefined });

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("notifyIndexNow", () => {
  it("no-ops when INDEXNOW_KEY is unset", async () => {
    setTestEnv({ INDEXNOW_KEY: undefined });
    const result = await notifyIndexNow(["/foo"]);
    expect(result).toEqual({ kind: "skipped", reason: "no-key" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("no-ops when paths is empty", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    const result = await notifyIndexNow([]);
    expect(result).toEqual({ kind: "skipped", reason: "no-paths" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("expands one path to one URL per locale and posts a single batch", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    await notifyIndexNow(["/u/wl"]);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [endpoint, init] = fetchMock.mock.calls[0];
    expect(endpoint).toBe("https://api.indexnow.org/indexnow");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.host).toBe("jseek.co");
    expect(body.key).toBe("test-key");
    expect(body.keyLocation).toBe("https://jseek.co/indexnow-key.txt");
    expect(body.urlList).toEqual([
      "https://jseek.co/en/u/wl",
      "https://jseek.co/de/u/wl",
      "https://jseek.co/fr/u/wl",
      "https://jseek.co/it/u/wl",
    ]);
  });

  it("encodes path segments with spaces and unicode", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    await notifyIndexNow(["/user name/lübeck-jobs"]);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.urlList[0]).toBe(
      "https://jseek.co/en/user%20name/l%C3%BCbeck-jobs",
    );
  });

  it("dedupes URLs across paths and pins ordering", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    await notifyIndexNow(["/foo", "/foo"]);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.urlList).toEqual([
      "https://jseek.co/en/foo",
      "https://jseek.co/de/foo",
      "https://jseek.co/fr/foo",
      "https://jseek.co/it/foo",
    ]);
  });

  it("caps the batch at 10_000 URLs (IndexNow protocol limit)", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    // 2_501 paths × 4 locales = 10_004 expanded URLs → must be trimmed.
    const paths = Array.from({ length: 2_501 }, (_, i) => `/p${i}`);
    await notifyIndexNow(paths);
    expect(fetchMock).toHaveBeenCalledOnce();
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.urlList).toHaveLength(10_000);
  });

  it("does not log on a 200 response (success is silent)", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    await notifyIndexNow(["/foo"]);
    expect(errSpy).not.toHaveBeenCalled();
  });

  it("returns rejected (does not throw) when the endpoint rejects with 4xx", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    fetchMock.mockResolvedValue(new Response("bad key", { status: 403 }));
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const result = await notifyIndexNow(["/foo"]);
    expect(result).toEqual({ kind: "rejected", status: 403, urlCount: 4 });
    expect(errSpy).toHaveBeenCalledOnce();
    expect(String(errSpy.mock.calls[0][0])).toContain("403");
  });

  it("returns errored (does not throw) on network errors", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    const networkErr = new Error("ECONNRESET");
    fetchMock.mockRejectedValue(networkErr);
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const result = await notifyIndexNow(["/foo"]);
    expect(result).toEqual({ kind: "errored", error: networkErr, urlCount: 4 });
    expect(errSpy).toHaveBeenCalledOnce();
  });

  it("returns submitted with the URL count on a 200 response", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    const result = await notifyIndexNow(["/foo"]);
    expect(result).toEqual({ kind: "submitted", status: 200, urlCount: 4 });
  });

  it("restricts locale fan-out when availableLocales is provided (#2843, blog)", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    await notifyIndexNow(["/blog/welcome"], ["en", "de"]);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.urlList).toEqual([
      "https://jseek.co/en/blog/welcome",
      "https://jseek.co/de/blog/welcome",
    ]);
    expect(body.urlList).not.toContain("https://jseek.co/fr/blog/welcome");
    expect(body.urlList).not.toContain("https://jseek.co/it/blog/welcome");
  });

  it("emits a single locale URL for an EN-only post", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    await notifyIndexNow(["/blog/en-only"], ["en"]);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.urlList).toEqual(["https://jseek.co/en/blog/en-only"]);
  });

  it("no-ops when availableLocales is an empty array (defensive)", async () => {
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    const result = await notifyIndexNow(["/blog/no-locales"], []);
    expect(result).toEqual({ kind: "skipped", reason: "no-locales" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not import next/server (caller owns after() wrapping)", () => {
    // Structural regression guard. The original bug was that
    // notifyIndexNow called after() internally, which silently failed
    // when callers wrapped it inside a detached `_getOwnerInfo(...)
    // .then(...)` chain (request scope already torn down). The fix
    // moved after() up to the call sites in
    // apps/web/src/lib/actions/watchlists.ts. A future refactor that
    // re-imports `after` from "next/server" into indexnow.ts would
    // re-introduce the same failure mode.
    const src = readFileSync(
      join(__dirname, "..", "indexnow.ts"),
      "utf-8",
    );
    expect(src).not.toMatch(/from\s+["']next\/server["']/);
  });

  it("awaits the fetch (returns only after the POST resolves)", async () => {
    // Regression guard: an earlier shape wrapped fetch in next/server
    // after() internally and returned synchronously, which broke when
    // callers wrapped notifyIndexNow in a detached .then() chain. The
    // current contract is: notifyIndexNow awaits fetch directly and
    // the caller is responsible for invoking it inside after().
    setTestEnv({ INDEXNOW_KEY: "test-key" });
    let resolved = false;
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    fetchMock.mockImplementation(async () => {
      await gate;
      return new Response(null, { status: 200 });
    });
    const promise = notifyIndexNow(["/foo"]).then(() => {
      resolved = true;
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(resolved).toBe(false);
    release();
    await promise;
    expect(resolved).toBe(true);
  });
});
