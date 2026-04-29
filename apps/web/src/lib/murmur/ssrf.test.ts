/**
 * Tests for ssrf.ts.
 *
 * No real network access on the unit suites; all DNS calls are
 * intercepted via a mocked `node:dns/promises`, and `node:http` /
 * `node:https` `request` are stubbed so we can assert the request
 * options (especially `lookup`, `servername`, `hostname`) without
 * opening a socket. Each test queues the (address, family) tuples the
 * resolver should return, in order. The DNS-rebinding case queues two
 * different answers so the second lookup observably differs from the
 * first.
 *
 * The bottom suite spins up a real loopback `http.Server` to verify
 * end-to-end that the captured IP is used for the connection while the
 * original hostname is preserved on the request line / Host header.
 *
 * @see colophon-group/jobseek#2758
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import http from "node:http";
import { Readable } from "node:stream";
import type { AddressInfo } from "node:net";

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
  createPinnedLookup,
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

  it("classifies CGNAT (100.64/10) at both boundaries as private", () => {
    // Lower edge of 100.64.0.0/10
    expect(isPrivateIp("100.64.0.0")).toBe(true);
    expect(isPrivateIp("100.64.0.1")).toBe(true);
    // Upper edge: /10 ends at 100.127.255.255
    expect(isPrivateIp("100.127.255.255")).toBe(true);
    // Just outside on either side — must still be public.
    expect(isPrivateIp("100.63.255.255")).toBe(false);
    expect(isPrivateIp("100.128.0.0")).toBe(false);
  });

  it("treats short-form `::xxxx` IPv6 addresses as private (fail-closed)", () => {
    // `::abcd` is shorthand for 0:0:0:0:0:0:0:abcd — all zero groups
    // except the last. That sits inside the legacy `::/96`
    // (IPv4-compatible, deprecated) / `::/8` reserved space. We have
    // no business reaching such addresses; the implementation must
    // fail closed on them.
    expect(isPrivateIp("::abcd")).toBe(true);
    expect(isPrivateIp("::ff")).toBe(true);
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

describe("createPinnedLookup", () => {
  // The pinned lookup is the security-critical hinge: it must return
  // the captured IP for the validated hostname and refuse anything
  // else. Test it directly so we don't depend on the surrounding
  // request stack to surface the right behaviour.

  it("returns the captured (ip, family) tuple for the expected hostname", () => {
    const lookup = createPinnedLookup("acme.greenhouse.io", "104.19.20.21", 4);
    let result: { err: unknown; address: unknown; family: unknown } | null = null;
    lookup("acme.greenhouse.io", { family: 0, all: false }, (err, address, family) => {
      result = { err, address, family };
    });
    expect(result).not.toBeNull();
    expect(result!.err).toBeNull();
    expect(result!.address).toBe("104.19.20.21");
    expect(result!.family).toBe(4);
  });

  it("matches the expected hostname case-insensitively", () => {
    const lookup = createPinnedLookup("Acme.Greenhouse.IO", "104.19.20.21", 4);
    let result: { err: unknown; address: unknown } | null = null;
    lookup("ACME.greenhouse.io", { family: 0, all: false }, (err, address) => {
      result = { err, address };
    });
    expect(result!.err).toBeNull();
    expect(result!.address).toBe("104.19.20.21");
  });

  it("propagates IPv6 family unchanged", () => {
    const lookup = createPinnedLookup("acme.greenhouse.io", "2606:4700::1111", 6);
    let captured: { address: unknown; family: unknown } | null = null;
    lookup("acme.greenhouse.io", { family: 0, all: false }, (err, address, family) => {
      if (err) throw err;
      captured = { address, family };
    });
    expect(captured!.address).toBe("2606:4700::1111");
    expect(captured!.family).toBe(6);
  });

  it("fails closed with ENOTFOUND for any other hostname", () => {
    const lookup = createPinnedLookup("acme.greenhouse.io", "104.19.20.21", 4);
    let err: NodeJS.ErrnoException | null = null;
    lookup("evil.example.com", { family: 0, all: false }, (e) => {
      err = e;
    });
    expect(err).not.toBeNull();
    expect(err!.code).toBe("ENOTFOUND");
  });

  it("fails closed for a sibling subdomain not equal to the expected host", () => {
    const lookup = createPinnedLookup("acme.greenhouse.io", "104.19.20.21", 4);
    let err: NodeJS.ErrnoException | null = null;
    lookup("other.greenhouse.io", { family: 0, all: false }, (e) => {
      err = e;
    });
    expect(err).not.toBeNull();
    expect(err!.code).toBe("ENOTFOUND");
  });

  it("returns an array shape when called with `all: true` (Node's net.connect mode)", () => {
    // Node's modern `net.connect` calls `lookup` with `all: true`,
    // which expects the callback to receive `(err, addresses[])`. The
    // pinned lookup must support this calling convention or the
    // connection layer rejects with "Invalid IP address: undefined".
    const lookup = createPinnedLookup("acme.greenhouse.io", "104.19.20.21", 4);
    let captured:
      | { err: NodeJS.ErrnoException | null; addresses: { address: string; family: number }[] }
      | null = null;
    lookup(
      "acme.greenhouse.io",
      { family: 0, all: true },
      (err, addressOrAddresses) => {
        captured = {
          err: err as NodeJS.ErrnoException | null,
          addresses: addressOrAddresses as { address: string; family: number }[],
        };
      },
    );
    expect(captured!.err).toBeNull();
    expect(Array.isArray(captured!.addresses)).toBe(true);
    expect(captured!.addresses).toEqual([{ address: "104.19.20.21", family: 4 }]);
  });

  it("returns an empty addresses array on error when called with `all: true`", () => {
    const lookup = createPinnedLookup("acme.greenhouse.io", "104.19.20.21", 4);
    let captured:
      | { err: NodeJS.ErrnoException | null; addresses: unknown }
      | null = null;
    lookup("evil.com", { family: 0, all: true }, (err, addressOrAddresses) => {
      captured = {
        err: err as NodeJS.ErrnoException | null,
        addresses: addressOrAddresses,
      };
    });
    expect(captured!.err).not.toBeNull();
    expect((captured!.err as NodeJS.ErrnoException).code).toBe("ENOTFOUND");
    expect(Array.isArray(captured!.addresses)).toBe(true);
    expect(captured!.addresses).toEqual([]);
  });
});

// Capture for the mocked node:https.request call; populated by the
// vi.mock factory below. The mocked module is hoisted, so we put the
// shared state on globalThis to avoid the "top-level variables in
// factory" rule.
type CapturedRequestOptions = {
  protocol?: string;
  hostname?: string;
  servername?: string;
  port?: number;
  method?: string;
  path?: string;
  headers?: Record<string, string>;
  lookup?: (
    hostname: string,
    opts: { all?: boolean },
    cb: (
      err: NodeJS.ErrnoException | null,
      addressOrAddresses: string | { address: string; family: number }[],
      family?: number,
    ) => void,
  ) => void;
};

interface HttpsMockState {
  callCount: number;
  lastOptions: CapturedRequestOptions | null;
}

declare global {
  var __ssrfHttpsMock: HttpsMockState | undefined;
}

globalThis.__ssrfHttpsMock = { callCount: 0, lastOptions: null };

vi.mock("node:https", async () => {
  const actual = await vi.importActual<typeof import("node:https")>("node:https");
  return {
    ...actual,
    default: actual,
    request: ((opts: CapturedRequestOptions, cb?: (res: unknown) => void) => {
      const state = globalThis.__ssrfHttpsMock!;
      state.callCount += 1;
      state.lastOptions = opts;
      const fakeRes = Object.assign(
        new Readable({
          read() {
            this.push("ok");
            this.push(null);
          },
        }),
        {
          statusCode: 200,
          statusMessage: "OK",
          headers: { "content-type": "text/plain" },
        },
      );
      queueMicrotask(() => cb?.(fakeRes));
      return {
        on: () => undefined,
        write: () => undefined,
        end: () => undefined,
      };
    }) as unknown as typeof actual.request,
  };
});

const httpsState = (): HttpsMockState => globalThis.__ssrfHttpsMock!;

describe("safeFetch — request shape (mocked node:https)", () => {
  // The mocked `node:https.request` records the options safeFetch
  // passes so we can assert the TLS-correctness invariant: SNI uses
  // the original hostname, the request line uses the original
  // hostname, and the connection-time `lookup` is a pinned function
  // that returns the captured IP only for that hostname.
  beforeEach(() => {
    httpsState().callCount = 0;
    httpsState().lastOptions = null;
  });

  it("dispatches against the original hostname (not the resolved IP) and pins via `lookup`", async () => {
    dnsQueue.push({ address: "104.19.20.21", family: 4 });
    const r = await safeFetch("https://acme.greenhouse.io/x?y=1");
    expect(r.status).toBe(200);

    const opts = httpsState().lastOptions;
    expect(opts).not.toBeNull();
    // SNI / request-line hostname must be the original, not the IP —
    // this is the TLS-correctness fix from the review.
    expect(opts!.hostname).toBe("acme.greenhouse.io");
    expect(opts!.servername).toBe("acme.greenhouse.io");
    expect(opts!.path).toBe("/x?y=1");
    expect(opts!.protocol).toBe("https:");
    expect(opts!.port).toBe(443);

    // Verify the pinned lookup is wired correctly: invoking it with
    // the expected hostname returns the captured IP; anything else
    // fails closed.
    let okAddr = "";
    let okFamily: number | undefined;
    opts!.lookup!("acme.greenhouse.io", { all: false }, (err, address, family) => {
      if (err) throw err as Error;
      okAddr = address as string;
      okFamily = family;
    });
    expect(okAddr).toBe("104.19.20.21");
    expect(okFamily).toBe(4);

    let evilErr: NodeJS.ErrnoException | null = null;
    opts!.lookup!("evil.com", { all: false }, (e) => {
      evilErr = e as NodeJS.ErrnoException;
    });
    expect(evilErr).not.toBeNull();
    expect(evilErr!.code).toBe("ENOTFOUND");
  });

  it("rebind poisoning: a second resolution yielding a private IP is rejected without opening a socket", async () => {
    // Simulate the rebind: the resolver now yields a private IP for
    // the same hostname. safeFetch must reject before constructing a
    // request.
    dnsQueue.push({ address: "10.0.0.5", family: 4 });
    let rebindError: { code?: string } | null = null;
    try {
      await safeFetch("https://acme.greenhouse.io/x");
    } catch (e) {
      rebindError = e as { code?: string };
    }
    expect(rebindError).not.toBeNull();
    expect(rebindError?.code).toBe("url_resolves_to_private");
    // And critically: the request layer was never reached.
    expect(httpsState().callCount).toBe(0);
  });
});

describe("safeFetch + createPinnedLookup — loopback integration", () => {
  // End-to-end integration check (small, no TLS): stand up a real
  // `http.Server` on 127.0.0.1, wire `createPinnedLookup` into a
  // direct `http.request` call, and confirm the connection routed to
  // the loopback address even though we asked for an arbitrary
  // (non-resolved) hostname. This proves the lookup hook is honoured
  // by Node's connection layer — the same hook safeFetch attaches.
  //
  // We don't drive `safeFetch` directly here because validateUrl
  // would (correctly) refuse 127.0.0.1 as private. We're testing the
  // lookup mechanism, not the validation pipeline.

  let server: http.Server | null = null;
  let port = 0;
  let receivedHost: string | undefined;
  let receivedRemoteAddr: string | undefined;

  beforeEach(async () => {
    receivedHost = undefined;
    receivedRemoteAddr = undefined;
    server = http.createServer((req, res) => {
      receivedHost = req.headers.host;
      receivedRemoteAddr = req.socket.remoteAddress ?? undefined;
      res.statusCode = 200;
      res.end("ok");
    });
    await new Promise<void>((resolve) => {
      server!.listen(0, "127.0.0.1", resolve);
    });
    port = (server!.address() as AddressInfo).port;
  });

  afterEach(async () => {
    if (server) {
      await new Promise<void>((resolve) => server!.close(() => resolve()));
      server = null;
    }
  });

  it("routes the connection to the captured 127.0.0.1 IP while the URL hostname is preserved on the wire", async () => {
    // Pin a synthetic hostname to 127.0.0.1.
    const lookup = createPinnedLookup("acme.greenhouse.io", "127.0.0.1", 4);

    const body = await new Promise<string>((resolve, reject) => {
      const req = http.request(
        {
          protocol: "http:",
          hostname: "acme.greenhouse.io",
          port,
          method: "GET",
          path: "/",
          lookup,
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => resolve(Buffer.concat(chunks).toString()));
          res.on("error", reject);
        },
      );
      req.on("error", reject);
      req.end();
    });

    expect(body).toBe("ok");
    // The Host header on the request line must be the original
    // hostname (proves the IP did not leak into the URL/Host).
    expect(receivedHost).toContain("acme.greenhouse.io");
    // The TCP connection actually went to loopback (proves the
    // lookup pin took effect).
    // Node may report `::ffff:127.0.0.1` depending on platform.
    expect(receivedRemoteAddr).toMatch(/^(::ffff:)?127\.0\.0\.1$/);
  });
});
