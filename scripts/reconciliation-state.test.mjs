import assert from "node:assert/strict";
import { copyFileSync, chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { after, test } from "node:test";

const root = mkdtempSync(join(tmpdir(), "native-reconciliation-state-"));
after(() => rmSync(root, { recursive: true, force: true }));
const reference = "apps/crawler/tests/fixtures/reconciliation_state_reference.py";
const selected = process.env.JOBSEEK_NATIVE_RECONCILIATION_BINARY;
const built = selected || join(root, "crawler-deploy-tools");
if (!selected) {
  const head = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
  assert.equal(head.status, 0, head.stderr);
  const build = spawnSync("go", [
    "-C", "apps/crawler/go/deploy-tools", "build",
    "-ldflags", "-X main.sourceRevision=" + head.stdout.trim(),
    "-o", built, ".",
  ], { env: { ...process.env, GOWORK: "off" }, encoding: "utf8", timeout: 120_000 });
  assert.equal(build.status, 0, build.stderr);
}
const alias = join(root, "jobseek-reconciliation-state");
copyFileSync(built, alias);
chmodSync(alias, 0o755);
const identity = spawnSync(alias, ["--build-info"], { encoding: "utf8" });
assert.equal(identity.status, 0, identity.stderr);
const revision = JSON.parse(identity.stdout).source_revision;
assert.match(revision, /^[0-9a-f]{40}$/);
const digest = "b".repeat(64);
const other = revision === "a".repeat(40) ? "c".repeat(40) : "a".repeat(40);

for (const scenario of [
  "missing-revision", "invalid-revision", "missing-wrapper", "invalid-wrapper",
  "success", "whitespace", "revision-mismatch", "wrapper-mismatch",
]) {
  test("actual Python/native reconciliation command parity: " + scenario, () => {
    const state = mkdtempSync(join(root, "state-"));
    const args = ["--state-root", state, "check"];
    if (scenario !== "missing-revision") {
      writeFileSync(join(state, "deployed-sha"),
        scenario === "invalid-revision" ? "corrupt\n" :
          scenario === "whitespace" ? " \t\x1c" + revision + "\r\n\v" : revision + "\n");
    }
    if (!["missing-revision", "invalid-revision", "missing-wrapper"].includes(scenario)) {
      writeFileSync(join(state, "wrapper-sha256"),
        scenario === "invalid-wrapper" ? "corrupt\n" : digest + "\n");
    }
    if (scenario === "revision-mismatch") args.push("--expected-revision", other);
    if (scenario === "wrapper-mismatch") args.push("--expected-wrapper-sha256", "c".repeat(64));
    const original = spawnSync("python3", [reference, ...args], { encoding: "utf8", timeout: 10_000 });
    const native = spawnSync(alias, args, { encoding: "utf8", timeout: 10_000 });
    assert.equal(native.status, original.status);
    assert.equal(native.stdout, original.stdout);
    assert.equal(native.stderr, original.stderr);
  });
}

test("native state attestation binds the compiled source independently of caller expectations", () => {
  const state = mkdtempSync(join(root, "foreign-"));
  writeFileSync(join(state, "deployed-sha"), other + "\n");
  writeFileSync(join(state, "wrapper-sha256"), digest + "\n");
  const result = spawnSync(alias, ["--state-root", state, "check"], { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, /compiled source/);
});

test("native installed alias preserves the original install privilege boundary", () => {
  const state = join(root, "install");
  mkdirSync(state);
  const group = spawnSync("id", ["-gn"], { encoding: "utf8" });
  assert.equal(group.status, 0, group.stderr);
  const args = ["--state-root", state, "install", "--revision", revision,
    "--wrapper-sha256", digest, "--group", group.stdout.trim()];
  const original = spawnSync("python3", [reference, ...args], { encoding: "utf8" });
  const native = spawnSync(alias, args, { encoding: "utf8" });
  assert.equal(native.status, original.status);
  assert.equal(native.stdout, original.stdout);
  assert.equal(native.stderr, original.stderr);
});
