/**
 * Validator for the `final_output` payload against the accept dialect.
 *
 * Same spirit as the J5 `validateBody`: emit per-field errors as
 * `{ path, message }` pairs with stable string tokens; never throw on a
 * weird body. The accept dialect adds `type: "array"` and recursive
 * object schemas, so this validator is a slight extension rather than a
 * reuse of the J5 one.
 *
 * Errors are formatted by the route handler as
 * `validation:<path>:<message>` per the M0 envelope contract — see
 * jobseek#2763.
 *
 * @see colophon-group/jobseek#2763
 */

import type {
  AcceptObjectSchema,
  AcceptPropertySchema,
} from "./accept-schema";

export interface AcceptSchemaError {
  readonly path: string;
  readonly message: string;
}

/**
 * Validate an unknown body against the top-level `final_output` schema.
 *
 * Returns an empty array on success and a list of per-field errors
 * otherwise. The list is stable-ordered (object key order then
 * recursion).
 */
export function validateAcceptBody(
  _body: unknown,
  _schema: AcceptObjectSchema,
): AcceptSchemaError[] {
  throw new Error("not implemented");
}

/**
 * Recursive helper that validates `value` against a single property
 * schema. Exported for unit-testing edge cases (deep arrays of
 * objects).
 */
export function validateAcceptProperty(
  _path: string,
  _value: unknown,
  _schema: AcceptPropertySchema,
  _errors: AcceptSchemaError[],
): void {
  throw new Error("not implemented");
}
