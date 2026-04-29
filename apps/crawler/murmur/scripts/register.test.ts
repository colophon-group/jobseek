/**
 * Tests for register.ts (jobseek#2761, P2).
 *
 * Verification matrix from the issue:
 *
 *   1. `pnpm register-pipeline` against a stub Murmur server (vitest mock)
 *      succeeds with a valid YAML.
 *   2. Same script against a stub returning 4xx exits with non-zero.
 *   3. Same script with `MURMUR_URL` or `MURMUR_TOKEN` unset exits with a
 *      clear error message.
 *   4. Idempotent: running twice with the same YAML succeeds (Murmur's
 *      last-write-wins).
 *
 * Quality gates from the issue:
 *
 *   5. Script does not log the token.
 *   6. Script reads YAML once and posts the JSON-converted body
 *      (server-side parsing happens; no double-conversion bugs) — i.e.
 *      `def_yaml` on the wire is the raw file contents verbatim.
 */
import { describe, it, expect } from "vitest";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  buildPipelinesUrl,
  checkEnv,
  extractPipelineId,
  formatFailure,
  main,
  parseArgs,
  postPipeline,
  readYamlRaw,
  type FetchImpl,
  type PostPipelineResult,
  type RegisterEnv,
  type WritableStreamLike,
} from "./register.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(HERE, "..");
const YAML_PATH = path.join(PKG_ROOT, "pipelines", "add-company.yaml");

const FAKE_TOKEN = "test-token-do-not-log-me-0123456789ABCDEFGHIJ";
const FAKE_URL = "https://murmur.example.test";

interface CapturedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string;
}

interface FakeFetchOptions {
  /** Response status to return. Default 200. */
  status?: number;
  /** Response body shape. Default `{ ok: true, data: { id: "<id>" } }`. */
  body?: unknown;
  /** If set, the fake throws to simulate transport failure. */
  throwError?: Error;
}

/**
 * Build a fake fetch + capture array. Each call appends to the array
 * and returns the configured response.
 */
function makeFakeFetch(
  options: FakeFetchOptions = {},
): { fetchImpl: FetchImpl; calls: CapturedRequest[] } {
  const calls: CapturedRequest[] = [];
  const fetchImpl = ((async (url: string | URL | Request, init?: RequestInit) => {
    if (options.throwError) throw options.throwError;
    const headers: Record<string, string> = {};
    const initHeaders = init?.headers as Record<string, string> | undefined;
    if (initHeaders) {
      for (const [k, v] of Object.entries(initHeaders)) headers[k] = v;
    }
    calls.push({
      url: typeof url === "string" ? url : url.toString(),
      method: init?.method ?? "GET",
      headers,
      body: typeof init?.body === "string" ? init.body : "",
    });
    const status = options.status ?? 200;
    const body =
      options.body === undefined
        ? { ok: true, data: { id: "jobseek-add-company" } }
        : options.body;
    const text =
      typeof body === "string" ? body : JSON.stringify(body);
    return new Response(text, {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown) as FetchImpl;
  return { fetchImpl, calls };
}

class StringStream implements WritableStreamLike {
  buf = "";
  write(chunk: string): boolean {
    this.buf += chunk;
    return true;
  }
}

function freshStreams(): { stdout: StringStream; stderr: StringStream } {
  return { stdout: new StringStream(), stderr: new StringStream() };
}

const VALID_ENV: RegisterEnv = {
  MURMUR_URL: FAKE_URL,
  MURMUR_TOKEN: FAKE_TOKEN,
};

describe("readYamlRaw", () => {
  it("reads the committed pipeline YAML as text", () => {
    const text = readYamlRaw(YAML_PATH);
    expect(text).toBeTypeOf("string");
    expect(text.length).toBeGreaterThan(0);
    expect(text).toContain("id: jobseek-add-company");
  });

  it("throws on a missing file", () => {
    expect(() => readYamlRaw(path.join(HERE, "does-not-exist.yaml"))).toThrow();
  });
});

describe("extractPipelineId", () => {
  it("returns the top-level id from the committed YAML", () => {
    const text = readYamlRaw(YAML_PATH);
    expect(extractPipelineId(text)).toBe("jobseek-add-company");
  });

  it("throws when the YAML has no id", () => {
    expect(() => extractPipelineId("subtasks: []\n")).toThrow(/id/i);
  });

  it("throws when YAML parses to a non-object (array)", () => {
    expect(() => extractPipelineId("- one\n- two\n")).toThrow();
  });

  it("throws on completely malformed YAML", () => {
    expect(() => extractPipelineId(":\n  : :\n  -")).toThrow();
  });
});

describe("buildPipelinesUrl", () => {
  it("appends /pipelines to a clean base", () => {
    expect(buildPipelinesUrl("https://m.example")).toBe(
      "https://m.example/pipelines",
    );
  });

  it("trims trailing slashes", () => {
    expect(buildPipelinesUrl("https://m.example///")).toBe(
      "https://m.example/pipelines",
    );
  });
});

describe("checkEnv", () => {
  it("returns no missing names when both vars are set", () => {
    expect(checkEnv(VALID_ENV)).toEqual([]);
  });

  it("flags MURMUR_URL when missing", () => {
    expect(checkEnv({ MURMUR_TOKEN: FAKE_TOKEN })).toEqual(["MURMUR_URL"]);
  });

  it("flags MURMUR_TOKEN when missing", () => {
    expect(checkEnv({ MURMUR_URL: FAKE_URL })).toEqual(["MURMUR_TOKEN"]);
  });

  it("flags both when both are missing", () => {
    expect(checkEnv({})).toEqual(["MURMUR_URL", "MURMUR_TOKEN"]);
  });

  it("treats empty string as missing", () => {
    expect(checkEnv({ MURMUR_URL: "", MURMUR_TOKEN: "" })).toEqual([
      "MURMUR_URL",
      "MURMUR_TOKEN",
    ]);
  });
});

describe("postPipeline", () => {
  it("POSTs JSON with bearer auth and returns the parsed envelope", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ status: 200 });
    const result = await postPipeline(
      `${FAKE_URL}/pipelines`,
      FAKE_TOKEN,
      { id: "jobseek-add-company", def_yaml: "id: jobseek-add-company\n" },
      fetchImpl,
    );
    expect(result.status).toBe(200);
    expect("ok" in result.body && result.body.ok).toBe(true);
    expect(calls).toHaveLength(1);
    const call = calls[0]!;
    expect(call.method).toBe("POST");
    expect(call.url).toBe(`${FAKE_URL}/pipelines`);
    expect(call.headers["Authorization"]).toBe(`Bearer ${FAKE_TOKEN}`);
    expect(call.headers["Content-Type"]).toBe("application/json");
    const sent = JSON.parse(call.body) as { id: string; def_yaml: string };
    expect(sent.id).toBe("jobseek-add-company");
    expect(sent.def_yaml).toBe("id: jobseek-add-company\n");
  });

  it("returns parsed err envelope on 4xx without throwing", async () => {
    const { fetchImpl } = makeFakeFetch({
      status: 400,
      body: { ok: false, errors: ["validation:/id:bad"] },
    });
    const result = await postPipeline(
      `${FAKE_URL}/pipelines`,
      FAKE_TOKEN,
      { id: "x", def_yaml: "id: x\n" },
      fetchImpl,
    );
    expect(result.status).toBe(400);
    expect("ok" in result.body && result.body.ok === false).toBe(true);
  });

  it("propagates transport errors", async () => {
    const { fetchImpl } = makeFakeFetch({ throwError: new Error("ECONNREFUSED") });
    await expect(
      postPipeline(
        `${FAKE_URL}/pipelines`,
        FAKE_TOKEN,
        { id: "x", def_yaml: "id: x\n" },
        fetchImpl,
      ),
    ).rejects.toThrow(/ECONNREFUSED/);
  });

  it("falls back to {raw: ...} when the body isn't JSON", async () => {
    const { fetchImpl } = makeFakeFetch({ status: 502, body: "<html>nope</html>" });
    const result = await postPipeline(
      `${FAKE_URL}/pipelines`,
      FAKE_TOKEN,
      { id: "x", def_yaml: "id: x\n" },
      fetchImpl,
    );
    expect(result.status).toBe(502);
    expect("raw" in result.body).toBe(true);
  });
});

describe("formatFailure", () => {
  it("includes status and errors for an err envelope", () => {
    const r: PostPipelineResult = {
      status: 400,
      body: { ok: false, errors: ["validation:/id"] },
    };
    expect(formatFailure(r)).toContain("HTTP 400");
    expect(formatFailure(r)).toContain("validation:/id");
  });

  it("handles non-JSON bodies", () => {
    const r: PostPipelineResult = { status: 502, body: { raw: "<html>" } };
    expect(formatFailure(r)).toContain("HTTP 502");
    expect(formatFailure(r)).toContain("non-JSON");
  });
});

describe("parseArgs", () => {
  it("returns the single positional yaml path", () => {
    expect(parseArgs(["pipelines/x.yaml"]).yamlPath).toBe("pipelines/x.yaml");
  });

  it("returns yamlPath: null on empty argv", () => {
    expect(parseArgs([]).yamlPath).toBeNull();
  });

  it("rejects unknown flags", () => {
    expect(() => parseArgs(["--what"])).toThrow();
  });

  it("rejects multiple positional args", () => {
    expect(() => parseArgs(["a.yaml", "b.yaml"])).toThrow();
  });
});

describe("main — verification cases", () => {
  it("(1) succeeds with a valid YAML against a 200 stub", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ status: 200 });
    const { stdout, stderr } = freshStreams();
    const code = await main(
      [YAML_PATH],
      VALID_ENV,
      fetchImpl,
      stdout,
      stderr,
    );
    expect(code).toBe(0);
    expect(stderr.buf).toBe("");
    expect(stdout.buf).toContain("registered pipeline");
    expect(stdout.buf).toContain("jobseek-add-company");
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe(`${FAKE_URL}/pipelines`);
  });

  it("(2) exits non-zero against a 4xx stub", async () => {
    const { fetchImpl } = makeFakeFetch({
      status: 400,
      body: { ok: false, errors: ["validation:/id:bad"] },
    });
    const { stdout, stderr } = freshStreams();
    const code = await main(
      [YAML_PATH],
      VALID_ENV,
      fetchImpl,
      stdout,
      stderr,
    );
    expect(code).not.toBe(0);
    expect(stderr.buf).toContain("HTTP 400");
    expect(stderr.buf).toContain("validation:/id:bad");
  });

  it("(3a) MURMUR_URL unset — clear error message naming the var", async () => {
    const { fetchImpl, calls } = makeFakeFetch();
    const { stdout, stderr } = freshStreams();
    const code = await main(
      [YAML_PATH],
      { MURMUR_TOKEN: FAKE_TOKEN },
      fetchImpl,
      stdout,
      stderr,
    );
    expect(code).not.toBe(0);
    expect(stderr.buf).toContain("MURMUR_URL");
    expect(calls).toHaveLength(0);
  });

  it("(3b) MURMUR_TOKEN unset — clear error message naming the var", async () => {
    const { fetchImpl, calls } = makeFakeFetch();
    const { stdout, stderr } = freshStreams();
    const code = await main(
      [YAML_PATH],
      { MURMUR_URL: FAKE_URL },
      fetchImpl,
      stdout,
      stderr,
    );
    expect(code).not.toBe(0);
    expect(stderr.buf).toContain("MURMUR_TOKEN");
    expect(calls).toHaveLength(0);
  });

  it("(3c) MURMUR_TOKEN missing-error never echoes a token-shaped value", async () => {
    // Even though no token was provided, double-check the error message
    // does not echo any value next to the variable name.
    const sneakyEnv: RegisterEnv = { MURMUR_URL: FAKE_URL };
    const { fetchImpl } = makeFakeFetch();
    const { stdout, stderr } = freshStreams();
    await main([YAML_PATH], sneakyEnv, fetchImpl, stdout, stderr);
    // Generic guard — no equals sign after MURMUR_TOKEN that would
    // indicate accidental "MURMUR_TOKEN=<value>" leakage.
    expect(stderr.buf).not.toMatch(/MURMUR_TOKEN\s*=/);
  });

  it("(4) idempotent: two consecutive 200 responses both succeed", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ status: 200 });
    const first = freshStreams();
    const second = freshStreams();

    const code1 = await main(
      [YAML_PATH],
      VALID_ENV,
      fetchImpl,
      first.stdout,
      first.stderr,
    );
    const code2 = await main(
      [YAML_PATH],
      VALID_ENV,
      fetchImpl,
      second.stdout,
      second.stderr,
    );

    expect(code1).toBe(0);
    expect(code2).toBe(0);
    expect(calls).toHaveLength(2);
    // Both calls send identical bodies — bit-identical, no
    // re-serialisation churn.
    expect(calls[0]!.body).toBe(calls[1]!.body);
  });
});

describe("main — quality gates", () => {
  it("(5) never logs the token to stdout/stderr (200 path)", async () => {
    const { fetchImpl } = makeFakeFetch({ status: 200 });
    const { stdout, stderr } = freshStreams();
    await main([YAML_PATH], VALID_ENV, fetchImpl, stdout, stderr);
    expect(stdout.buf).not.toContain(FAKE_TOKEN);
    expect(stderr.buf).not.toContain(FAKE_TOKEN);
  });

  it("(5) never logs the token to stdout/stderr (4xx path)", async () => {
    const { fetchImpl } = makeFakeFetch({
      status: 401,
      body: { ok: false, errors: ["unauthorised"] },
    });
    const { stdout, stderr } = freshStreams();
    await main([YAML_PATH], VALID_ENV, fetchImpl, stdout, stderr);
    expect(stdout.buf).not.toContain(FAKE_TOKEN);
    expect(stderr.buf).not.toContain(FAKE_TOKEN);
  });

  it("(5) never logs the token on transport-error path", async () => {
    const { fetchImpl } = makeFakeFetch({
      throwError: new Error("ECONNREFUSED"),
    });
    const { stdout, stderr } = freshStreams();
    const code = await main([YAML_PATH], VALID_ENV, fetchImpl, stdout, stderr);
    expect(code).toBe(1);
    expect(stdout.buf).not.toContain(FAKE_TOKEN);
    expect(stderr.buf).not.toContain(FAKE_TOKEN);
  });

  it("(6) reads YAML once and sends the raw text — no double-conversion", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ status: 200 });
    const { stdout, stderr } = freshStreams();
    await main([YAML_PATH], VALID_ENV, fetchImpl, stdout, stderr);
    expect(calls).toHaveLength(1);
    const sent = JSON.parse(calls[0]!.body) as {
      id: string;
      def_yaml: string;
    };
    const onDisk = readFileSync(YAML_PATH, "utf-8");
    // Bit-identical: the YAML on the wire equals the YAML on disk.
    expect(sent.def_yaml).toBe(onDisk);
    // The committed YAML uses kebab-case id; the request must echo it.
    expect(sent.id).toBe("jobseek-add-company");
    // And only the two expected keys are on the body — no leakage.
    const sentKeys = Object.keys(JSON.parse(calls[0]!.body)).sort();
    expect(sentKeys).toEqual(["def_yaml", "id"]);
  });

  it("(6) sends the bearer token in Authorization header (and only there)", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ status: 200 });
    const { stdout, stderr } = freshStreams();
    await main([YAML_PATH], VALID_ENV, fetchImpl, stdout, stderr);
    const call = calls[0]!;
    expect(call.headers["Authorization"]).toBe(`Bearer ${FAKE_TOKEN}`);
    // Token must NOT appear in the body.
    expect(call.body).not.toContain(FAKE_TOKEN);
    // Token must NOT appear in the URL.
    expect(call.url).not.toContain(FAKE_TOKEN);
  });
});

describe("main — usage / argv errors", () => {
  it("returns 2 and writes usage line when no argv given", async () => {
    const { fetchImpl } = makeFakeFetch();
    const { stdout, stderr } = freshStreams();
    const code = await main([], VALID_ENV, fetchImpl, stdout, stderr);
    expect(code).toBe(2);
    expect(stderr.buf).toContain("usage");
  });

  it("returns 2 when the YAML file does not exist", async () => {
    const { fetchImpl, calls } = makeFakeFetch();
    const { stdout, stderr } = freshStreams();
    const code = await main(
      [path.join(HERE, "does-not-exist.yaml")],
      VALID_ENV,
      fetchImpl,
      stdout,
      stderr,
    );
    expect(code).toBe(2);
    expect(stderr.buf).toContain("does-not-exist");
    expect(calls).toHaveLength(0);
  });

  it("returns 1 on transport failure", async () => {
    const { fetchImpl } = makeFakeFetch({
      throwError: new Error("ECONNREFUSED"),
    });
    const { stdout, stderr } = freshStreams();
    const code = await main([YAML_PATH], VALID_ENV, fetchImpl, stdout, stderr);
    expect(code).toBe(1);
    expect(stderr.buf).toContain("transport error");
  });
});
