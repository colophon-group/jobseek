import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock server-only to prevent import error
vi.mock("server-only", () => ({}));

/**
 * The cookie helper module imports `next/headers` (`cookies()`). We mock
 * it with an in-memory store so tests can drive both read + write paths
 * deterministically without booting a Next request lifecycle.
 */
const _store = new Map<string, string>();
const _deleted = new Set<string>();
const cookieMock = {
  get: (name: string) => (_store.has(name) ? { name, value: _store.get(name)! } : undefined),
  set: (
    name: string,
    value: string,
    _opts?: { sameSite?: string; maxAge?: number; path?: string; secure?: boolean },
  ) => {
    _store.set(name, value);
    _deleted.delete(name);
  },
  delete: (name: string) => {
    _store.delete(name);
    _deleted.add(name);
  },
};
vi.mock("next/headers", () => ({
  cookies: () => Promise.resolve(cookieMock),
}));

import {
  JOB_LANGUAGES_COOKIE,
  readAnonJobLanguagesCookie,
  writeAnonJobLanguagesCookie,
} from "../anon-preferences";

describe("anon-preferences cookie helper (#2850)", () => {
  beforeEach(() => {
    _store.clear();
    _deleted.clear();
  });

  describe("readAnonJobLanguagesCookie", () => {
    it("returns null when the cookie is absent", async () => {
      expect(await readAnonJobLanguagesCookie()).toBeNull();
    });

    it("returns null when the cookie value is not valid JSON", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, "not-json");
      expect(await readAnonJobLanguagesCookie()).toBeNull();
    });

    it("returns null when the parsed value is not an array", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, JSON.stringify({ fr: true }));
      expect(await readAnonJobLanguagesCookie()).toBeNull();
    });

    it("returns null when an array element is not a string", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, JSON.stringify(["en", 1]));
      expect(await readAnonJobLanguagesCookie()).toBeNull();
    });

    it("returns the array verbatim when all codes are known", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, JSON.stringify(["en", "fr"]));
      expect(await readAnonJobLanguagesCookie()).toEqual(["en", "fr"]);
    });

    it("preserves the all-languages sentinel `*`", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, JSON.stringify(["*"]));
      expect(await readAnonJobLanguagesCookie()).toEqual(["*"]);
    });

    it("drops unknown language codes silently", async () => {
      _store.set(
        JOB_LANGUAGES_COOKIE,
        JSON.stringify(["en", "totally-not-a-language", "fr"]),
      );
      expect(await readAnonJobLanguagesCookie()).toEqual(["en", "fr"]);
    });

    it("returns [] for a literal empty-array cookie", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, JSON.stringify([]));
      expect(await readAnonJobLanguagesCookie()).toEqual([]);
    });
  });

  describe("writeAnonJobLanguagesCookie", () => {
    it("writes a valid array as JSON", async () => {
      await writeAnonJobLanguagesCookie(["en", "fr"]);
      expect(_store.get(JOB_LANGUAGES_COOKIE)).toBe('["en","fr"]');
    });

    it("preserves the all-languages sentinel", async () => {
      await writeAnonJobLanguagesCookie(["*"]);
      expect(_store.get(JOB_LANGUAGES_COOKIE)).toBe('["*"]');
    });

    it("filters out unknown codes before writing", async () => {
      await writeAnonJobLanguagesCookie(["en", "bogus", "fr"]);
      expect(_store.get(JOB_LANGUAGES_COOKIE)).toBe('["en","fr"]');
    });

    it("deletes the cookie when the input is empty", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, '["fr"]');
      await writeAnonJobLanguagesCookie([]);
      expect(_store.has(JOB_LANGUAGES_COOKIE)).toBe(false);
      expect(_deleted.has(JOB_LANGUAGES_COOKIE)).toBe(true);
    });

    it("deletes the cookie when every input code is unknown", async () => {
      _store.set(JOB_LANGUAGES_COOKIE, '["fr"]');
      await writeAnonJobLanguagesCookie(["bogus", "alsobogus"]);
      expect(_store.has(JOB_LANGUAGES_COOKIE)).toBe(false);
      expect(_deleted.has(JOB_LANGUAGES_COOKIE)).toBe(true);
    });
  });

  describe("round-trip", () => {
    it("write → read returns the same shape", async () => {
      await writeAnonJobLanguagesCookie(["en", "fr"]);
      expect(await readAnonJobLanguagesCookie()).toEqual(["en", "fr"]);
    });

    it("write * → read returns [\"*\"]", async () => {
      await writeAnonJobLanguagesCookie(["*"]);
      expect(await readAnonJobLanguagesCookie()).toEqual(["*"]);
    });
  });
});
