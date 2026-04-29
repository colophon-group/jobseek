/**
 * Compose-shape contract for the crawler-box deployment of murmur-shim.
 *
 * H3 (#2775): the shim runs as a sidecar in `apps/crawler/docker-compose.yml`.
 * Because the crawler box is fully containerized — no host-side venv exists —
 * the shim relies on a one-shot init service that uses the crawler image
 * to populate a named volume with python interpreter + stdlib + venv
 * site-packages + crawler `src/`. The shim mounts that volume read-only at
 * the paths H2's Dockerfile defaults expect:
 *
 *     MURMUR_PY=/opt/jobseek-crawler-venv/bin/python3
 *     MURMUR_CRAWLER_ROOT=/opt/jobseek-crawler-src
 *
 * These tests assert the compose YAML wires that contract correctly,
 * without invoking docker (so they run in CI without docker-in-docker).
 *
 * Source spec: colophon-group/jobseek#2775.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";
import { parse as parseYaml } from "yaml";

const composePath = path.resolve(
  __dirname,
  "../../../crawler/docker-compose.yml",
);

interface ComposeService {
  image?: string;
  restart?: string;
  network_mode?: string;
  mem_limit?: string;
  command?: string | readonly string[];
  environment?: Record<string, string> | readonly string[];
  volumes?: readonly string[];
  healthcheck?: {
    test?: readonly string[] | string;
    interval?: string;
    timeout?: string;
    start_period?: string;
    retries?: number;
  };
  depends_on?:
    | readonly string[]
    | Record<string, { condition?: string }>;
  pull_policy?: string;
}

interface ComposeFile {
  services: Record<string, ComposeService>;
  volumes?: Record<string, unknown>;
}

function loadCompose(): ComposeFile {
  const raw = readFileSync(composePath, "utf8");
  return parseYaml(raw) as ComposeFile;
}

describe("crawler docker-compose: murmur-shim service", () => {
  it("compose YAML parses cleanly", () => {
    const compose = loadCompose();
    expect(compose).toBeTruthy();
    expect(compose.services).toBeTruthy();
  });

  it("declares a `murmur-shim` service", () => {
    const compose = loadCompose();
    expect(compose.services["murmur-shim"]).toBeTruthy();
  });

  it("pins the shim image to ghcr.io/<owner>/jobseek-murmur-shim with a tag override", () => {
    const compose = loadCompose();
    const image = compose.services["murmur-shim"]?.image ?? "";
    // OWNER substitution + tag override let H4 deploy individual SHAs.
    expect(image).toContain("jobseek-murmur-shim");
    expect(image).toMatch(/\$\{?SHIM_IMAGE_TAG/);
  });

  it("uses `network_mode: host` so it can reach the local Postgres + redis", () => {
    const compose = loadCompose();
    expect(compose.services["murmur-shim"]?.network_mode).toBe("host");
  });

  it("has explicit mem_limit and restart: unless-stopped", () => {
    const compose = loadCompose();
    const svc = compose.services["murmur-shim"];
    expect(svc?.restart).toBe("unless-stopped");
    expect(svc?.mem_limit).toBeTruthy();
  });

  it("references all required env vars (the names the H2 Dockerfile + invoke-lib read)", () => {
    const compose = loadCompose();
    const env = compose.services["murmur-shim"]?.environment ?? [];
    const flat = normalizeEnvironment(env);
    // PORT — the standalone Next server reads PORT.
    expect(flat).toContain("PORT");
    // MURMUR_TOKEN — auth bearer (forwarded value).
    expect(flat).toContain("MURMUR_TOKEN");
    // MURMUR_DB_DSN — the shim's invoke-lib + claim-kv read this. We wire
    // it to LOCAL_DATABASE_URL (the Hetzner Postgres machine, same DSN
    // the crawler workers use); the legacy DATABASE_URL points at
    // Supabase which is exporter-only.
    expect(flat).toContain("MURMUR_DB_DSN");
    // MURMUR_ACCEPT_TARGET — selects postgres vs csv claim KV.
    expect(flat).toContain("MURMUR_ACCEPT_TARGET");
    // The python invocation contract. H2 defaults these but we set them
    // explicitly here to keep the wiring discoverable.
    expect(flat).toContain("MURMUR_PY");
    expect(flat).toContain("MURMUR_CRAWLER_ROOT");
  });

  it("mounts the crawler-runtime volume read-only at the H2 contract paths", () => {
    const compose = loadCompose();
    const volumes = compose.services["murmur-shim"]?.volumes ?? [];
    // Volume entries are strings of the form `name:/path:ro`. We assert
    // both the venv mount and the src mount exist and are :ro.
    const venvMount = volumes.find((v) => v.includes("/opt/jobseek-crawler-venv"));
    const srcMount = volumes.find((v) => v.includes("/opt/jobseek-crawler-src"));
    expect(venvMount).toBeTruthy();
    expect(srcMount).toBeTruthy();
    expect(venvMount).toMatch(/:ro$/);
    expect(srcMount).toMatch(/:ro$/);
  });

  it("has a healthcheck that hits /health with reasonable interval and start-period", () => {
    const compose = loadCompose();
    const hc = compose.services["murmur-shim"]?.healthcheck;
    expect(hc).toBeTruthy();
    // The test command should reference /health.
    const testCmd = Array.isArray(hc?.test) ? hc?.test.join(" ") : hc?.test ?? "";
    expect(testCmd).toContain("/health");
    // Reasonable cadence: not faster than 5s (would be log-noisy), not
    // slower than 60s (delays detection of a crashloop).
    expect(hc?.interval).toMatch(/^(5|10|15|30|60)s$/);
    // start_period must be > 0 so the container has time to boot Next.
    expect(hc?.start_period).toBeTruthy();
    expect(hc?.start_period).not.toBe("0s");
  });

  it("depends on the runtime-init service completing successfully", () => {
    const compose = loadCompose();
    const dep = compose.services["murmur-shim"]?.depends_on;
    // depends_on can be array or object; we want the object form so we
    // can express service_completed_successfully.
    expect(typeof dep).toBe("object");
    expect(Array.isArray(dep)).toBe(false);
    const depObj = dep as Record<string, { condition?: string }>;
    expect(depObj["murmur-shim-runtime-init"]).toBeTruthy();
    expect(depObj["murmur-shim-runtime-init"]?.condition).toBe(
      "service_completed_successfully",
    );
  });
});

describe("crawler docker-compose: murmur-shim-runtime-init", () => {
  it("uses the crawler image so it has the same venv + stdlib as the workers", () => {
    const compose = loadCompose();
    const init = compose.services["murmur-shim-runtime-init"];
    expect(init).toBeTruthy();
    expect(init?.image ?? "").toContain("jobseek-crawler:");
  });

  it("forces a fresh image pull on every up so a new crawler image rehydrates the volume", () => {
    // Without `pull_policy: always`, compose only pulls when the tag
    // changes. We deploy with `:latest`, so a content change behind
    // `:latest` would not trigger the init copy without an explicit
    // pull_policy.
    const compose = loadCompose();
    expect(compose.services["murmur-shim-runtime-init"]?.pull_policy).toBe("always");
  });

  it("populates the named volume at /runtime", () => {
    const compose = loadCompose();
    const volumes = compose.services["murmur-shim-runtime-init"]?.volumes ?? [];
    const runtimeMount = volumes.find((v) => v.endsWith(":/runtime"));
    expect(runtimeMount).toBeTruthy();
    // Init must have RW access — no `:ro` suffix.
    expect(runtimeMount).not.toMatch(/:ro$/);
  });

  it("does not auto-restart (one-shot)", () => {
    const compose = loadCompose();
    // `restart: "no"` keeps the init from re-running indefinitely after
    // the copy completes. The shim's depends_on with
    // `service_completed_successfully` is the contract that gates the
    // shim on the init's exit-zero.
    expect(compose.services["murmur-shim-runtime-init"]?.restart).toBe("no");
  });
});

describe("crawler docker-compose: top-level volumes", () => {
  it("declares the murmur-shim-runtime named volume", () => {
    const compose = loadCompose();
    expect(compose.volumes).toBeTruthy();
    expect(compose.volumes?.["murmur-shim-runtime"]).toBeDefined();
  });
});

/**
 * Compose lets `environment:` be either a list (`["FOO=bar", "BAZ"]`) or a
 * map (`{FOO: bar, BAZ: ""}`). Normalize to the set of variable names so
 * the assertions above are consistent across either form.
 */
function normalizeEnvironment(
  env: Record<string, string> | readonly string[],
): readonly string[] {
  if (Array.isArray(env)) {
    return env.map((entry) => {
      const eq = entry.indexOf("=");
      return eq === -1 ? entry : entry.slice(0, eq);
    });
  }
  return Object.keys(env as Record<string, string>);
}
