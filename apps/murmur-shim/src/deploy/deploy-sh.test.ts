/**
 * Fail-closed crawler deployment contract while Murmur is paused.
 *
 * Source spec: colophon-group/jobseek#8814.
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

describe("crawler deploy.sh: paused Murmur integration", () => {
  it("does not require or persist Murmur credentials and image identity", () => {
    const sh = loadDeploySh();

    expect(sh).not.toContain("MURMUR_TOKEN");
    expect(sh).not.toContain("SHIM_IMAGE_REF");
    expect(sh).not.toContain("murmur-shim");
  });

  it("starts an explicit crawler-only service allowlist without removing parked containers", () => {
    const sh = loadDeploySh();

    expect(sh).toContain("CRAWLER_STACK_SERVICES=(");
    expect(sh).toContain(
      'docker compose up -d "${CRAWLER_STACK_SERVICES[@]}"',
    );
    expect(sh).not.toMatch(/^[ \t]*docker compose .*--remove-orphans/m);
  });

  it("keeps the existing transactional environment rollback", () => {
    const sh = loadDeploySh();

    expect(sh).toContain("set -euo pipefail");
    expect(sh).toContain('ENV_FILE="$DEPLOY_DIR/.env"');
    expect(sh).toContain('ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"');
    expect(sh).toContain(
      'install -m 0600 "$ACTIVE_ENV_SNAPSHOT" "$ROLLBACK_ENV_FILE"',
    );
    expect(sh).toContain('cat > "$ENV_FILE" <<EOF');
  });
});
