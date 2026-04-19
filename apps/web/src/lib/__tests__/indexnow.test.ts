import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { notifyIndexNow } from "../indexnow";

const ORIGINAL_KEY = process.env.INDEXNOW_KEY;
const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  if (ORIGINAL_KEY === undefined) delete process.env.INDEXNOW_KEY;
  else process.env.INDEXNOW_KEY = ORIGINAL_KEY;
  vi.restoreAllMocks();
});

describe("notifyIndexNow", () => {
  it("no-ops when INDEXNOW_KEY is unset", async () => {
    delete process.env.INDEXNOW_KEY;
    await notifyIndexNow(["/foo"]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("no-ops when paths is empty", async () => {
    process.env.INDEXNOW_KEY = "test-key";
    await notifyIndexNow([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("expands one path to one URL per locale and posts a single batch", async () => {
    process.env.INDEXNOW_KEY = "test-key";
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
    process.env.INDEXNOW_KEY = "test-key";
    await notifyIndexNow(["/user name/lübeck-jobs"]);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.urlList[0]).toBe(
      "https://jseek.co/en/user%20name/l%C3%BCbeck-jobs",
    );
  });

  it("dedupes URLs across paths", async () => {
    process.env.INDEXNOW_KEY = "test-key";
    await notifyIndexNow(["/foo", "/foo"]);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.urlList).toHaveLength(4); // 4 locales × 1 unique path
  });

  it("logs (does not throw) when the endpoint rejects with 4xx", async () => {
    process.env.INDEXNOW_KEY = "test-key";
    fetchMock.mockResolvedValue(new Response("bad key", { status: 403 }));
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(notifyIndexNow(["/foo"])).resolves.toBeUndefined();
    expect(errSpy).toHaveBeenCalledOnce();
  });

  it("logs (does not throw) on network errors", async () => {
    process.env.INDEXNOW_KEY = "test-key";
    fetchMock.mockRejectedValue(new Error("ECONNRESET"));
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(notifyIndexNow(["/foo"])).resolves.toBeUndefined();
    expect(errSpy).toHaveBeenCalledOnce();
  });

  it("awaits the fetch (returns only after the POST resolves)", async () => {
    // Regression guard: an earlier shape wrapped fetch in next/server
    // after() internally and returned synchronously, which broke when
    // callers wrapped notifyIndexNow in a detached .then() chain. The
    // current contract is: notifyIndexNow awaits fetch directly and
    // the caller is responsible for invoking it inside after().
    process.env.INDEXNOW_KEY = "test-key";
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
