import assert from "node:assert/strict";
import {
  chmodSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

function mockExecutable(path, source) {
  writeFileSync(path, `#!/usr/bin/env node\n${source}`);
  chmodSync(path, 0o755);
}

function runHygiene({ containers = [], units = {} }) {
  const dir = mkdtempSync(join(tmpdir(), "crawler-host-hygiene-"));
  const uptime = join(dir, "uptime");
  writeFileSync(uptime, "1000000.00 0.00\n");

  mockExecutable(
    join(dir, "docker"),
    `const containers = JSON.parse(process.env.MOCK_CONTAINERS);
const command = process.argv[2];
if (command === "ps") {
  process.stdout.write(containers.map((item) => item.Id).join("\\n"));
} else if (command === "inspect") {
  process.stdout.write(JSON.stringify(containers));
} else {
  process.exit(2);
}
`,
  );
  mockExecutable(
    join(dir, "systemctl"),
    `const units = JSON.parse(process.env.MOCK_UNITS);
const command = process.argv[2];
if (command === "list-units") {
  process.stdout.write(Object.entries(units).map(([name, item]) =>
    \`${"${name}"} loaded active ${"${item.subState}"} fixture\`
  ).join("\\n"));
} else if (command === "show") {
  const item = units[process.argv[3]];
  process.stdout.write(\`FragmentPath=${"${item.fragment}"}\\nActiveEnterTimestampMonotonic=${"${item.activeAtMicros}"}\\n\`);
} else {
  process.exit(2);
}
`,
  );

  const result = spawnSync(
    "python3",
    [
      "scripts/crawler-host-hygiene.py",
      "--now",
      "2026-07-20T12:00:00Z",
      "--proc-uptime",
      uptime,
    ],
    {
      cwd: process.cwd(),
      env: {
        ...process.env,
        PATH: `${dir}:${process.env.PATH}`,
        MOCK_CONTAINERS: JSON.stringify(containers),
        MOCK_UNITS: JSON.stringify(units),
      },
      encoding: "utf8",
    },
  );
  rmSync(dir, { recursive: true, force: true });
  return result;
}

function container({ id, name, startedAt, composeProject = "" }) {
  return {
    Id: id,
    Name: `/${name}`,
    Config: {
      Image: "crawler-full:test",
      Labels: composeProject
        ? { "com.docker.compose.project": composeProject }
        : {},
    },
    State: { StartedAt: startedAt },
  };
}

test("host hygiene reports only stale unmanaged resources", () => {
  const result = runHygiene({
    containers: [
      container({
        id: "stale-container-id",
        name: "tesla-debug",
        startedAt: "2026-07-18T10:00:00.123456789Z",
      }),
      container({
        id: "compose-container-id",
        name: "worker",
        startedAt: "2026-07-10T10:00:00Z",
        composeProject: "deploy",
      }),
      container({
        id: "recent-container-id",
        name: "crawler-refresh-typesense",
        startedAt: "2026-07-20T11:30:00Z",
      }),
    ],
    units: {
      "starbucks-backfill.service": {
        subState: "exited",
        fragment: "/run/systemd/transient/starbucks-backfill.service",
        activeAtMicros: 500_000_000_000,
      },
      "recent-debug.service": {
        subState: "exited",
        fragment: "/run/systemd/transient/recent-debug.service",
        activeAtMicros: 999_000_000_000,
      },
      "managed.service": {
        subState: "exited",
        fragment: "/etc/systemd/system/managed.service",
        activeAtMicros: 100_000_000_000,
      },
      "running.service": {
        subState: "running",
        fragment: "/run/systemd/transient/running.service",
        activeAtMicros: 100_000_000_000,
      },
    },
  });

  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /found 2 stale resource/);
  assert.match(result.stderr, /tesla-debug/);
  assert.match(result.stderr, /docker rm -f -- tesla-debug/);
  assert.match(result.stderr, /starbucks-backfill\.service/);
  assert.match(result.stderr, /systemctl stop starbucks-backfill\.service/);
  assert.doesNotMatch(result.stderr, /worker/);
  assert.doesNotMatch(result.stderr, /crawler-refresh-typesense/);
  assert.doesNotMatch(result.stderr, /recent-debug/);
  assert.doesNotMatch(result.stderr, /managed\.service/);
  assert.doesNotMatch(result.stderr, /running\.service/);
});

test("host hygiene succeeds when old resources are managed", () => {
  const result = runHygiene({
    containers: [
      container({
        id: "compose-container-id",
        name: "worker",
        startedAt: "2026-07-10T10:00:00Z",
        composeProject: "deploy",
      }),
    ],
    units: {
      "managed.service": {
        subState: "exited",
        fragment: "/etc/systemd/system/managed.service",
        activeAtMicros: 100_000_000_000,
      },
    },
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /host hygiene clean/);
});
