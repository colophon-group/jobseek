import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { BrowserContext } from "playwright";
import { parseProductionDeployment } from "../../../../.github/scripts/verify-vercel-promotion.mjs";
import { bootstrapDeploymentAccess } from "./deployment-access";

const execFileAsync = promisify(execFile);
export type CanaryTarget = { kind: "staged" | "production"; base: URL };
export type CanaryDeploymentIdentity = { sha: string; id: string; url: string };
function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw Object.assign(new Error(code), { code });
}

/** Explicit modes: only the exact public alias or a single immutable Vercel host. */
export function resolveCanaryTarget(raw: string, mode = "staged"): CanaryTarget {
  check(mode === "staged" || mode === "production", "INVALID_CANARY_TARGET_MODE");
  const base = new URL(raw);
  check(base.protocol === "https:" && !base.username && !base.password && !base.port &&
    base.pathname === "/" && !base.search && !base.hash, "INVALID_CANARY_TARGET");
  if (mode === "production") {
    check(base.origin === "https://jseek.co", "INVALID_PUBLIC_CANARY_TARGET");
  } else {
    check(/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.vercel\.app$/i.test(base.hostname), "INVALID_STAGED_CANARY_TARGET");
  }
  return { kind: mode, base };
}

/** Public-origin requests never acquire, carry, or need a protection bypass. */
export async function openCanaryAccess(context: BrowserContext, target: CanaryTarget, bypass?: string) {
  resolveCanaryTarget(target.base.href, target.kind);
  if (target.kind === "production") return;
  check(typeof bypass === "string" && bypass.length > 0, "STAGED_CANARY_BYPASS_MISSING");
  await bootstrapDeploymentAccess(context, target.base, bypass);
}

export function validateCanaryDeploymentIdentity(expected: CanaryDeploymentIdentity) {
  check(/^[a-f0-9]{40}$/.test(expected.sha) && /^dpl_[A-Za-z0-9]+$/.test(expected.id), "INVALID_CANARY_DEPLOYMENT_IDENTITY");
  return { ...expected, url: resolveCanaryTarget(expected.url).base.origin };
}

/** Management GET only. Raw output, CLI stderr and command exceptions stay private. */
export async function readPublicAliasDeployment(environment: Record<string, string | undefined> = process.env) {
  check(/^team_[A-Za-z0-9]+$/.test(environment.VERCEL_ORG_ID ?? "") && Boolean(environment.VERCEL_TOKEN), "PUBLIC_CANARY_PROVIDER_CONFIGURATION_MISSING");
  try {
    const { stdout } = await execFileAsync("pnpm", ["dlx", "vercel@59.25.4", "api",
      `/v13/deployments/jseek.co?teamId=${environment.VERCEL_ORG_ID}`, "--raw", `--token=${environment.VERCEL_TOKEN}`],
    { encoding: "utf8", maxBuffer: 1_000_000, timeout: 30_000 });
    return stdout;
  } catch {
    // execFile errors retain command arguments and credential-bearing stderr.
    throw Object.assign(new Error("PUBLIC_CANARY_ALIAS_LOOKUP_UNAVAILABLE"), { code: "PUBLIC_CANARY_ALIAS_LOOKUP_UNAVAILABLE" });
  }
}

export async function reattestPublicCanaryIdentity(expected: CanaryDeploymentIdentity, fetchAlias = readPublicAliasDeployment) {
  const validated = validateCanaryDeploymentIdentity(expected);
  try {
    const actual = parseProductionDeployment(await fetchAlias());
    check(actual.sha === validated.sha && actual.id === validated.id && new URL(actual.url).origin === validated.url,
      "PUBLIC_CANARY_ALIAS_IDENTITY_MISMATCH");
  } catch {
    throw Object.assign(new Error("PUBLIC_CANARY_ALIAS_IDENTITY_NOT_PROVEN"), { code: "PUBLIC_CANARY_ALIAS_IDENTITY_NOT_PROVEN" });
  }
}
