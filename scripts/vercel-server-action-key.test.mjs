import assert from "node:assert/strict";
import test from "node:test";
import {
  SERVER_ACTION_KEY_NAME,
  verifyServerActionEnvironment,
  verifyServerActionKey,
} from "../.github/scripts/verify-vercel-server-action-key.mjs";

const validKey = Buffer.alloc(32, 0x5a).toString("base64");

test("accepts a canonical base64 32-byte Server Action key", () => {
  assert.doesNotThrow(() => verifyServerActionKey(validKey));
  assert.doesNotThrow(() => verifyServerActionEnvironment({
    [SERVER_ACTION_KEY_NAME]: validKey,
  }));
});

test("rejects an absent Server Action key", () => {
  assert.throws(
    () => verifyServerActionEnvironment({ OTHER_SETTING: "present" }),
    new RegExp(`must define ${SERVER_ACTION_KEY_NAME}`),
  );
});

test("rejects non-32-byte and non-canonical values without echoing them", () => {
  for (const invalidKey of [
    Buffer.alloc(16, 0x41).toString("base64"),
    Buffer.alloc(24, 0x42).toString("base64"),
    Buffer.alloc(33, 0x43).toString("base64"),
    `${validKey} `,
    "not-base64",
  ]) {
    assert.throws(
      () => verifyServerActionKey(invalidKey),
      (error) => {
        assert.match(error.message, /canonical base64 encoding exactly 32 bytes/);
        assert.doesNotMatch(error.message, new RegExp(invalidKey));
        return true;
      },
    );
  }
});

test("rejects a malformed injected key without exposing its content", () => {
  const malformed = '"unterminated';
  assert.throws(
    () => verifyServerActionEnvironment({
      [SERVER_ACTION_KEY_NAME]: malformed,
    }),
    (error) => {
      assert.match(
        error.message,
        /canonical base64 encoding exactly 32 bytes/,
      );
      assert.doesNotMatch(error.message, /unterminated/);
      return true;
    },
  );
});
