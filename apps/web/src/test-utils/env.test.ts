import { describe, expect, it } from "vitest";

import { restoreTestEnv, setTestEnv, snapshotTestEnv } from "./env";

describe("test env helpers", () => {
  it("restores absent and present environment variables", () => {
    const keys = [
      "JOBSEEK_TEST_ENV_HELPER_PRESENT",
      "JOBSEEK_TEST_ENV_HELPER_ABSENT",
    ];
    const outerSnapshot = snapshotTestEnv(keys);

    try {
      setTestEnv({
        JOBSEEK_TEST_ENV_HELPER_PRESENT: "original",
        JOBSEEK_TEST_ENV_HELPER_ABSENT: undefined,
      });

      const snapshot = snapshotTestEnv(keys);
      setTestEnv({
        JOBSEEK_TEST_ENV_HELPER_PRESENT: "changed",
        JOBSEEK_TEST_ENV_HELPER_ABSENT: "created",
      });

      restoreTestEnv(snapshot);

      expect(process.env.JOBSEEK_TEST_ENV_HELPER_PRESENT).toBe("original");
      expect(process.env.JOBSEEK_TEST_ENV_HELPER_ABSENT).toBeUndefined();
    } finally {
      restoreTestEnv(outerSnapshot);
    }
  });
});
