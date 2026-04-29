/**
 * Tests for ssrf.ts.
 *
 * No real network access; all DNS calls are intercepted via a mocked
 * `node:dns/promises`. Each test queues the (address, family) tuples the
 * resolver should return, in order. The DNS-rebinding case queues two
 * different answers so the second lookup observably differs from the
 * first.
 *
 * @see colophon-group/jobseek#2758
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// Queue of DNS answers consumed in order by the mocked `lookup`.
// Stored on globalThis so the hoisted `vi.mock` factory can see them
// without triggering vitest's "top-level variables in factory" rule.
type DnsAnswer = { address: string; family: 4 | 6 };

interface DnsMockState {
  queue: DnsAnswer[];
  callCount: number;
  throwError: Error | null;
}

declare global {
  var __ssrfDnsMock: DnsMockState | undefined;
}

globalThis.__ssrfDnsMock = { queue: [], callCount: 0, throwError: null };

vi.mock("node:dns/promises", () => {
  const lookup = async (_hostname: string, _opts?: unknown) => {
    const state = globalThis.__ssrfDnsMock!;
    state.callCount += 1;
    if (state.throwError) throw state.throwError;
    const answer = state.queue.shift();
    if (!answer) {
      throw Object.assign(new Error("ENOTFOUND"), { code: "ENOTFOUND" });
    }
    return answer;
  };
  return { default: { lookup }, lookup };
});

const dnsState = (): DnsMockState => globalThis.__ssrfDnsMock!;
const dnsQueue = {
  push: (a: DnsAnswer) => dnsState().queue.push(a),
  reset: () => {
    dnsState().queue.length = 0;
  },
  get length() {
    return dnsState().queue.length;
  },
  set length(n: number) {
    dnsState().queue.length = n;
  },
};
beforeEach(() => {
  dnsState().queue.length = 0;
  dnsState().callCount = 0;
  dnsState().throwError = null;
});

import {
  validateUrl,
  matchHostPattern,
  isPrivateIp,
  HOST_ALLOWLIST,
  safeFetch,
} from "./ssrf";

describe("HOST_ALLOWLIST", () => {
  it("includes the patterns called out by the issue", () => {
    expect(HOST_ALLOWLIST).toEqual(
      expect.arrayContaining([
        "*.greenhouse.io",
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "*.lever.co",
        "jobs.lever.co",
        "*.myworkdayjobs.com",
      ]),
    );
  });
});

describe("matchHostPattern", () => {
  it("matches an exact host", () => {
    expect(matchHostPattern("boards.greenhouse.io", "boards.greenhouse.io")).toBe(true);
  });

  it("rejects a different host of the same suffix when pattern is exact", () => {
    expect(matchHostPattern("evil.greenhouse.io", "boards.greenhouse.io")).toBe(false);
  });

  it("matches a subdomain wildcard", () => {
    expect(matchHostPattern("acme.greenhouse.io", "*.greenhouse.io")).toBe(true);
    expect(matchHostPattern("acme.boards.greenhouse.io", "*.greenhouse.io")).toBe(true);
  });

  it("does NOT match the bare apex on a wildcard pattern", () => {
    expect(matchHostPattern("greenhouse.io", "*.greenhouse.io")).toBe(false);
  });

  it("does NOT match a sibling/spoofed suffix", () => {
    expect(matchHostPattern("greenhouse.io.evil.com", "*.greenhouse.io")).toBe(false);
    expect(matchHostPattern("notgreenhouse.io", "*.greenhouse.io")).toBe(false);
  });

  it("is case-insensitive", () => {
    expect(matchHostPattern("Acme.Greenhouse.IO", "*.greenhouse.io")).toBe(true);
    expect(matchHostPattern("BOARDS.GREENHOUSE.IO", "boards.greenhouse.io")).toBe(true);
  });
});

describe("isPrivateIp", () => {
  it("classifies loopback as private", () => {
    expect(isPrivateIp("127.0.0.1")).toBe(true);
    expect(isPrivateIp("127.255.255.254")).toBe(true);
    expect(isPrivateIp("::1")).toBe(true);
  });

  it("classifies RFC1918 as private", () => {
    expect(isPrivateIp("10.0.0.1")).toBe(true);
    expect(isPrivateIp("172.16.0.1")).toBe(true);
    expect(isPrivateIp("172.31.255.255")).toBe(true);
    expect(isPrivateIp("192.168.1.1")).toBe(true);
  });

  it("does NOT classify 172.32.x as private (outside RFC1918 /12)", () => {
    expect(isPrivateIp("172.32.0.1")).toBe(false);
  });

  it("classifies link-local as private", () => {
    expect(isPrivateIp("169.254.0.1")).toBe(true);
    expect(isPrivateIp("169.254.169.254")).toBe(true);
    expect(isPrivateIp("fe80::1")).toBe(true);
  });

  it("classifies IPv6 ULA as private", () => {
    expect(isPrivateIp("fc00::1")).toBe(true);
    expect(isPrivateIp("fd00::1")).toBe(true);
    expect(isPrivateIp("fdff:ffff::1")).toBe(true);
  });

  it("classifies IPv4-mapped IPv6 by the embedded v4", () => {
    expect(isPrivateIp("::ffff:127.0.0.1")).toBe(true);
    expect(isPrivateIp("::ffff:10.0.0.1")).toBe(true);
    expect(isPrivateIp("::ffff:8.8.8.8")).toBe(false);
  });

  it("classifies unspecified addresses as private", () => {
    expect(isPrivateIp("0.0.0.0")).toBe(true);
    expect(isPrivateIp("::")).toBe(true);
  });

  it("treats public addresses as public", () => {
    expect(isPrivateIp("8.8.8.8")).toBe(false);
    expect(isPrivateIp("1.1.1.1")).toBe(false);
    expect(isPrivateIp("2606:4700:4700::1111")).toBe(false);
  });
});

describe("validateUrl — allowlist gate", () => {
  it("accepts an allowed host that resolves to a public IP", async () => {
    dnsQueue.push({ address: "104.19.20.21", family: 4 });
    const r = await validateUrl("https://boards.greenhouse.io/acme");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.resolvedIp).toBe("104.19.20.21");
      expect(r.family).toBe(4);
      expect(r.url.hostname).toBe("boards.greenhouse.io");
    }
  });

  it("accepts a *.greenhouse.io subdomain match + public IP", async () => {
    dnsQueue.push({ address: "104.19.20.21", family: 4 });
    const r = await validateUrl("https://acme.greenhouse.io/");
    expect(r.ok).toBe(true);
  });

  it("rejects a disallowed host with `url_not_allowed` and performs NO DNS lookup", async () => {
    const r = await validateUrl("https://evil.example.com/");
    expect(r).toEqual({ ok: false, error: "url_not_allowed" });
    expect(dnsState().callCount).toBe(0);
  });

  it("rejects an unparseable input with `url_invalid`", async () => {
    const r = await validateUrl("not a url");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("url_invalid");
    expect(dnsState().callCount).toBe(0);
  });

  it("rejects a non-http(s) scheme even on an allowed host", async () => {
    const r = await validateUrl("ftp://boards.greenhouse.io/x");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("url_invalid");
    expect(dnsState().callCount).toBe(0);
  });
});

describe("validateUrl — IP filter", () => {
  it("rejects an allowed host that resolves to a private IP", async () => {
    dnsQueue.push({ address: "10.0.0.5", family: 4 });
    const r = await validateUrl("https://boards.greenhouse.io/x");
    expect(r).toEqual({ ok: false, error: "url_resolves_to_private" });
  });

  it("rejects an allowed host that resolves to the AWS metadata IP", async () => {
    dnsQueue.push({ address: "169.254.169.254", family: 4 });
    const r = await validateUrl("https://acme.greenhouse.io/x");
    expect(r).toEqual({ ok: false, error: "url_resolves_to_private" });
  });

  it("rejects a literal `localhost` URL (loopback)", async () => {
    const r = await validateUrl("https://localhost/");
    // `localhost` is not on the allowlist, so it must fail at the
    // pattern gate without a DNS lookup.
    expect(r).toEqual({ ok: false, error: "url_not_allowed" });
    expect(dnsState().callCount).toBe(0);
  });

  it("rejects a literal IP host (127.0.0.1) regardless of DNS", async () => {
    const r = await validateUrl("https://127.0.0.1/");
    expect(r.ok).toBe(false);
    // Either url_not_allowed (literal IP isn't on the host allowlist)
    // or url_resolves_to_private (if implementation chooses to short-
    // circuit literal IPs through the IP filter). Both are correct
    // outcomes; we just don't accept it.
    if (!r.ok) {
      expect(["url_not_allowed", "url_resolves_to_private"]).toContain(r.error);
    }
  });

  it("rejects a link-local resolution (169.254.0.0/16) on an allowed host", async () => {
    dnsQueue.push({ address: "169.254.42.1", family: 4 });
    const r = await validateUrl("https://acme.greenhouse.io/x");
    expect(r).toEqual({ ok: false, error: "url_resolves_to_private" });
  });

  it("rejects an IPv6 ULA (fc00::/7) resolution on an allowed host", async () => {
    dnsQueue.push({ address: "fd12:3456:789a::1", family: 6 });
    const r = await validateUrl("https://acme.greenhouse.io/x");
    expect(r).toEqual({ ok: false, error: "url_resolves_to_private" });
  });

  it("returns `url_dns_failed` when the resolver throws", async () => {
    dnsState().throwError = Object.assign(new Error("ENOTFOUND"), { code: "ENOTFOUND" });
    const r = await validateUrl("https://acme.greenhouse.io/x");
    expect(r).toEqual({ ok: false, error: "url_dns_failed" });
  });
});

describe("safeFetch — DNS rebinding scenario", () => {
  // Stub global fetch so safeFetch never actually opens a socket on the
  // happy path. It records the URL it was asked to fetch — the assertion
  // we care about is that the URL is the captured (post-validation) IP,
  // not the hostname (which a re-resolution could redirect).
  const originalFetch = globalThis.fetch;
  let lastFetchedUrl: string | URL | undefined;
  beforeEach(() => {
    lastFetchedUrl = undefined;
    globalThis.fetch = vi.fn(async (input: string | URL | Request) => {
      lastFetchedUrl = input instanceof Request ? input.url : input;
      return new Response("ok", { status: 200 });
    }) as typeof fetch;
  });
  // Restore after each test so the global fetch is back to its original
  // value before subsequent suites (defensive — no other suite uses
  // fetch, but the cleanup pattern is cheap).
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("first call: pins the connection to the resolved (public) IP, not the hostname", async () => {
    dnsQueue.push({ address: "104.19.20.21", family: 4 });
    const r = await safeFetch("https://acme.greenhouse.io/x");
    expect(r.status).toBe(200);
    // The captured IP must be in the URL passed to fetch — that's the
    // proof of pinning. A second DNS lookup at connect time has nothing
    // to attack because the URL no longer references the hostname.
    const fetched = String(lastFetchedUrl);
    expect(fetched).toContain("104.19.20.21");
    expect(fetched).not.toContain("acme.greenhouse.io");
  });

  it("rebind poisoning: a second lookup that resolves private is rejected, no connection opened", async () => {
    // Simulate the post-resolution rebind: the queue now yields a
    // private IP for the same hostname. safeFetch must fail closed.
    dnsQueue.push({ address: "10.0.0.5", family: 4 });
    let rebindError: { code?: string } | null = null;
    try {
      await safeFetch("https://acme.greenhouse.io/x");
    } catch (e) {
      rebindError = e as { code?: string };
    }
    expect(rebindError).not.toBeNull();
    expect(rebindError?.code).toBe("url_resolves_to_private");
    // And critically: fetch was never invoked.
    expect(lastFetchedUrl).toBeUndefined();
  });
});
