import assert from "node:assert/strict";
import test from "node:test";

import {
  SmokeResponseError,
  verifySmokeResponse,
} from "../.github/scripts/verify-vercel-smoke-response.mjs";

test("accepts a rendered sign-in page", () => {
  assert.deepEqual(
    verifySmokeResponse("/en/sign-in", "200", "<h1>Sign in</h1>"),
    { route: "/en/sign-in", status: "200" },
  );
});

test("rejects a non-success response", () => {
  assert.throws(
    () => verifySmokeResponse("/en/sign-in", "503", "Sign in"),
    (error) => error instanceof SmokeResponseError && /HTTP 503/.test(error.message),
  );
});

test("rejects the Better Auth schema mismatch from incident 9223", () => {
  assert.throws(
    () => verifySmokeResponse("/en/sign-in", "200", "SCHEMA_MISMATCH"),
    /Better Auth schema error/,
  );
});

test("rejects a Next.js streamed server error hidden behind HTTP 200", () => {
  assert.throws(
    () => verifySmokeResponse("/en/sign-in", "200", '$RX("B:6","3039164293")'),
    /streamed server error/,
  );
});

test("rejects a successful response without the sign-in form", () => {
  assert.throws(
    () => verifySmokeResponse("/en/sign-in", "200", "<h1>Try again</h1>"),
    /does not contain the sign-in form/,
  );
});

test("does not confuse ordinary React stream instructions with errors", () => {
  assert.doesNotThrow(() =>
    verifySmokeResponse("/en/explore?wm=remote&q=python", "200", '$RC("B:1")'),
  );
});
