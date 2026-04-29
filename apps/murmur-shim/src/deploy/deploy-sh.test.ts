/**
 * `apps/crawler/deploy.sh` extension contract for H3.
 *
 * The shim deploy itself runs via `.github/workflows/deploy-murmur-shim.yml`
 * (does NOT call deploy.sh), so deploy.sh's only role for the shim is to
 * keep the box's `.env` in sync on full-stack crawler redeploys. Without
 * this, a `deploy-crawler-browser.yml` run after a shim deploy would
 * rewrite `/home/deploy/.env` from scratch and drop MURMUR_TOKEN,
 * silently breaking the shim on the next `docker compose up
 * --remove-orphans` line.
 *
 * Source spec: colophon-group/jobseek#2775.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const deployShPath = path.resolve(
  __dirname,
  "../../../crawler/deploy.sh",
);

function loadDeploySh(): string {
  return readFileSync(deployShPath, "utf8");
}

describe("crawler deploy.sh: murmur-shim integration", () => {
  it("validates MURMUR_TOKEN as a required env var", () => {
    const sh = loadDeploySh();
    // Required vars are listed in a `required_vars=( ... )` array.
    // Match conservatively: the line containing MURMUR_TOKEN must be
    // inside that array (i.e., precede the closing paren of required_vars).
    const start = sh.indexOf("required_vars=(");
    expect(start).toBeGreaterThanOrEqual(0);
    const end = sh.indexOf(")", start);
    expect(end).toBeGreaterThan(start);
    const block = sh.slice(start, end);
    expect(block).toContain("MURMUR_TOKEN");
  });

  it("writes MURMUR_TOKEN into the box's .env so compose substitution works on full-stack redeploys", () => {
    const sh = loadDeploySh();
    // The .env file is generated via a heredoc. The shim's compose
    // service references ${MURMUR_TOKEN} for env substitution; if that
    // line is missing from the heredoc, full-stack redeploys (which
    // overwrite .env wholesale) would empty MURMUR_TOKEN.
    expect(sh).toMatch(/MURMUR_TOKEN=\$\{MURMUR_TOKEN\}/);
  });

  it("remains idempotent: still uses set -euo pipefail and rewrites .env on each run", () => {
    const sh = loadDeploySh();
    expect(sh).toContain("set -euo pipefail");
    // The .env heredoc opens with `cat > "$DEPLOY_DIR/.env" <<EOF` —
    // unchanged shape, so re-running is idempotent w.r.t. the existing
    // pattern.
    expect(sh).toContain('cat > "$DEPLOY_DIR/.env" <<EOF');
  });
});
