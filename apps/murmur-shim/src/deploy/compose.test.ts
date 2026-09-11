/**
 * Fail-closed deployment contract while the Murmur integration is paused.
 *
 * Murmur's implementation remains buildable and tested, but the crawler
 * Compose project must not contain an entry point that can start it.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";
import { parse as parseYaml } from "yaml";

const composePath = path.resolve(
  __dirname,
  "../../../crawler/docker-compose.yml",
);

interface ComposeFile {
  services: Record<string, unknown>;
  volumes?: Record<string, unknown>;
}

function loadCompose(): ComposeFile {
  return parseYaml(readFileSync(composePath, "utf8")) as ComposeFile;
}

describe("crawler docker-compose: paused Murmur integration", () => {
  it("has no Murmur service or runtime-init deployment entry point", () => {
    const compose = loadCompose();

    expect(compose.services).toBeTruthy();
    expect(compose.services["murmur-shim"]).toBeUndefined();
    expect(compose.services["murmur-shim-runtime-init"]).toBeUndefined();
  });

  it("has no Murmur runtime volume or credential/image substitution", () => {
    const raw = readFileSync(composePath, "utf8");
    const compose = loadCompose();

    expect(compose.volumes?.["murmur-shim-runtime"]).toBeUndefined();
    expect(raw).not.toContain("SHIM_IMAGE_REF");
    expect(raw).not.toContain("MURMUR_TOKEN");
  });
});
