// @vitest-environment node
import { canaryFailureKind } from "../../../scripts/company-reference/canary-picker";
import { mkdtemp, writeFile, chmod, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { BrowserContext } from "playwright";
import { afterEach, describe, expect, test, vi } from "vitest";
import { openCanaryAccess, resolveCanaryTarget, reattestPublicCanaryIdentity, readPublicAliasDeployment, validateCanaryDeploymentIdentity } from "../../../scripts/company-reference/canary-target";

const bootstrap = vi.hoisted(() => vi.fn());
vi.mock("../../../scripts/company-reference/deployment-access", () => ({ bootstrapDeploymentAccess: bootstrap }));
const expected = { sha: "a".repeat(40), id: "dpl_Expected123", url: "https://jobseek-immutable.vercel.app" };
const provider = (changed: Record<string, unknown> = {}) => JSON.stringify({ id: expected.id, gitSource: { sha: expected.sha }, url: "jobseek-immutable.vercel.app", ...changed });
afterEach(() => { vi.unstubAllEnvs(); bootstrap.mockReset(); });

describe("exact target modes", () => {
  test("stage remains the default and accepts only an immutable platform host", () => {
    expect(resolveCanaryTarget(expected.url)).toMatchObject({ kind: "staged" });
    expect(() => resolveCanaryTarget("https://jseek.co")).toThrow();
  });
  test("public mode permits precisely the canonical HTTPS custom origin", () => {
    expect(resolveCanaryTarget("https://jseek.co", "production").base.origin).toBe("https://jseek.co");
    expect(() => resolveCanaryTarget(expected.url, "production")).toThrow();
    expect(() => resolveCanaryTarget("https://jseek.co", "preview")).toThrow();
  });
  test.each([
    "http://jseek.co", "https://www.jseek.co", "https://jseek.co.evil.test", "https://evil.test@jseek.co",
    "https://jseek.co:8443", "https://jseek.co/path", "https://jseek.co/?token=private", "https://jseek.co/#private",
  ])("public mode rejects %s before any credentialed request", value => {
    expect(() => resolveCanaryTarget(value, "production")).toThrow();
  });
  test.each([
    "http://jobseek-immutable.vercel.app", "https://other.jobseek.vercel.app", "https://vercel.app", "https://jobseek.vercel.app.evil.test",
    "https://user:password@jobseek.vercel.app", "https://jobseek.vercel.app:8443", "https://jobseek.vercel.app/path", "https://jobseek.vercel.app/?secret=x",
  ])("stage rejects unsafe immutable target %s", value => {
    expect(() => resolveCanaryTarget(value)).toThrow();
  });
});

test("public access never invokes bootstrap or uses a supplied bypass secret", async () => {
  const context = {} as BrowserContext;
  await openCanaryAccess(context, resolveCanaryTarget("https://jseek.co", "production"), "fixture-secret");
  expect(bootstrap).not.toHaveBeenCalled();
});
test("a forged public target is rejected at the access boundary too", async () => {
  await expect(openCanaryAccess({} as BrowserContext, { kind: "production", base: new URL("https://evil.test") }, "fixture-secret")).rejects.toThrow();
  expect(bootstrap).not.toHaveBeenCalled();
});
test("stage retains mandatory scoped bootstrap with exactly its deployment origin", async () => {
  const target = resolveCanaryTarget(expected.url), context = {} as BrowserContext;
  await expect(openCanaryAccess(context, target)).rejects.toMatchObject({ code: "STAGED_CANARY_BYPASS_MISSING" });
  expect(bootstrap).not.toHaveBeenCalled();
  await openCanaryAccess(context, target, "fixture-secret");
  expect(bootstrap).toHaveBeenCalledExactlyOnceWith(context, target.base, "fixture-secret");
});
test("exact immutable identity is required before a public provider lookup", async () => {
  expect(validateCanaryDeploymentIdentity(expected)).toEqual(expected);
  for (const changed of [{ sha: "main" }, { id: "dpl_private\n" }, { url: "https://jseek.co" }, { url: "https://jobseek-immutable.vercel.app/?secret=x" }]) {
    const fetch = vi.fn(async () => provider());
    await expect(reattestPublicCanaryIdentity({ ...expected, ...changed }, fetch)).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  }
});
test("provider identity must match deployment ID, source SHA and immutable URL", async () => {
  await expect(reattestPublicCanaryIdentity(expected, async () => provider())).resolves.toBeUndefined();
  for (const changed of [{ id: "dpl_Old" }, { gitSource: { sha: "b".repeat(40) } }, { url: "jobseek-other.vercel.app" }]) {
    await expect(reattestPublicCanaryIdentity(expected, async () => provider(changed))).rejects.toMatchObject({ code: "PUBLIC_CANARY_ALIAS_IDENTITY_NOT_PROVEN" });
  }
});
test("malformed or credential-bearing lookup failures escape only as a fixed code", async () => {
  for (const fetch of [async () => "private-user-token", async () => { throw new Error("--token=private-user-token stdout=private-body"); }]) {
    try { await reattestPublicCanaryIdentity(expected, fetch); throw new Error("must reject"); }
    catch (error) { expect(error).toMatchObject({ message: "PUBLIC_CANARY_ALIAS_IDENTITY_NOT_PROVEN", code: "PUBLIC_CANARY_ALIAS_IDENTITY_NOT_PROVEN" }); expect(JSON.stringify(error)).not.toContain("private"); }
  }
});
test("a later alias change is rejected even if the initial identity check passed", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(provider()).mockResolvedValueOnce(provider({ id: "dpl_Other" }));
  await reattestPublicCanaryIdentity(expected, fetch);
  await expect(reattestPublicCanaryIdentity(expected, fetch)).rejects.toMatchObject({ code: "PUBLIC_CANARY_ALIAS_IDENTITY_NOT_PROVEN" });
  expect(fetch).toHaveBeenCalledTimes(2);
});
test("actual credentialed CLI command failure never retains command, stdout or stderr", async () => {
  const directory = await mkdtemp(join(tmpdir(), "jobseek-canary-provider-"));
  try {
    const executable = join(directory, "pnpm");
    await writeFile(executable, "#!/bin/sh\nprintf 'private-provider-body'\nprintf 'private-provider-token' >&2\nexit 9\n");
    await chmod(executable, 0o700);
    vi.stubEnv("PATH", directory + ":" + process.env.PATH);
    try { await readPublicAliasDeployment({ VERCEL_ORG_ID: "team_fixture", VERCEL_TOKEN: "fixture-token-private" }); throw new Error("must reject"); }
    catch (error) {
      expect(error).toMatchObject({ message: "PUBLIC_CANARY_ALIAS_LOOKUP_UNAVAILABLE", code: "PUBLIC_CANARY_ALIAS_LOOKUP_UNAVAILABLE" });
      expect(JSON.stringify(error)).not.toContain("private");
      expect(error).not.toHaveProperty("cause");
      expect(error).not.toHaveProperty("stderr");
      expect(error).not.toHaveProperty("stdout");
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});


describe("canary failure diagnostics", () => {
  test.each([['TimeoutError', 'browser_timeout'], ['PostgresError', 'database_error'], ['TypeError', 'type_error'], ['private-user-string', 'operation_error']])("bounds name %s", (name, expected) => {
    expect(canaryFailureKind({name, message:'private locator/customer/password', stack:'private'})).toBe(expected);
  });
  test("throwing name getter cannot leak or mask failure", () => {
    expect(canaryFailureKind(Object.defineProperty({}, 'name', {get(){throw new Error('private');}}))).toBe('unknown_failure');
  });
});
