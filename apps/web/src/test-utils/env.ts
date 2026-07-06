import { afterAll, afterEach, beforeAll, beforeEach } from "vitest";

export type TestEnvValue = string | undefined;
export type TestEnvOverrides = Record<string, TestEnvValue>;

export function snapshotTestEnv(keys: Iterable<string>): TestEnvOverrides {
  const snapshot: TestEnvOverrides = {};
  for (const key of keys) {
    snapshot[key] = process.env[key];
  }
  return snapshot;
}

export function setTestEnv(overrides: TestEnvOverrides): void {
  for (const [key, value] of Object.entries(overrides)) {
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
}

export function restoreTestEnv(snapshot: TestEnvOverrides): void {
  setTestEnv(snapshot);
}

export function withTestEnv(overrides: TestEnvOverrides): void {
  let original: TestEnvOverrides | undefined;

  beforeEach(() => {
    original = snapshotTestEnv(Object.keys(overrides));
    setTestEnv(overrides);
  });

  afterEach(() => {
    if (original !== undefined) {
      restoreTestEnv(original);
      original = undefined;
    }
  });
}

export function withTestEnvForAll(overrides: TestEnvOverrides): void {
  let original: TestEnvOverrides | undefined;

  beforeAll(() => {
    original = snapshotTestEnv(Object.keys(overrides));
    setTestEnv(overrides);
  });

  afterAll(() => {
    if (original !== undefined) {
      restoreTestEnv(original);
      original = undefined;
    }
  });
}
