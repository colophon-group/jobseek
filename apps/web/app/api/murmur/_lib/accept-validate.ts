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
  body: unknown,
  schema: AcceptObjectSchema,
): AcceptSchemaError[] {
  const errors: AcceptSchemaError[] = [];
  if (!isPlainObject(body)) {
    errors.push({ path: "", message: "must_be_object" });
    return errors;
  }

  for (const key of schema.required) {
    if (!(key in body)) {
      errors.push({ path: `/${key}`, message: "missing" });
    }
  }
  for (const key of Object.keys(body)) {
    if (!(key in schema.properties)) {
      errors.push({ path: `/${key}`, message: "unknown_property" });
    }
  }
  for (const [key, propSchema] of Object.entries(schema.properties)) {
    if (!(key in body)) continue;
    const value = (body as Record<string, unknown>)[key];
    if (value === undefined) {
      // Explicitly-set `undefined` is treated like missing for the
      // required check above, but slips past `key in body`.
      errors.push({ path: `/${key}`, message: "missing" });
      continue;
    }
    validateAcceptProperty(`/${key}`, value, propSchema, errors);
  }
  return errors;
}

/**
 * Recursive helper that validates `value` against a single property
 * schema. Exported for unit-testing edge cases (deep arrays of
 * objects).
 */
export function validateAcceptProperty(
  path: string,
  value: unknown,
  schema: AcceptPropertySchema,
  errors: AcceptSchemaError[],
): void {
  switch (schema.type) {
    case "string": {
      if (typeof value !== "string") {
        errors.push({ path, message: "must_be_string" });
        return;
      }
      if (
        typeof schema.minLength === "number" &&
        value.length < schema.minLength
      ) {
        errors.push({ path, message: "too_short" });
      }
      if (
        typeof schema.maxLength === "number" &&
        value.length > schema.maxLength
      ) {
        errors.push({ path, message: "too_long" });
      }
      if (schema.pattern && !new RegExp(schema.pattern).test(value)) {
        errors.push({ path, message: "pattern_mismatch" });
      }
      if (schema.format === "uri") {
        try {
          const u = new URL(value);
          if (!u.hostname) {
            errors.push({ path, message: "must_be_uri" });
          }
        } catch {
          errors.push({ path, message: "must_be_uri" });
        }
      }
      if (schema.enum && !schema.enum.includes(value)) {
        errors.push({ path, message: "not_in_enum" });
      }
      return;
    }
    case "integer": {
      if (typeof value !== "number" || !Number.isInteger(value)) {
        errors.push({ path, message: "must_be_integer" });
        return;
      }
      if (typeof schema.minimum === "number" && value < schema.minimum) {
        errors.push({ path, message: "below_minimum" });
      }
      return;
    }
    case "number": {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        errors.push({ path, message: "must_be_number" });
        return;
      }
      if (typeof schema.minimum === "number" && value < schema.minimum) {
        errors.push({ path, message: "below_minimum" });
      }
      return;
    }
    case "boolean": {
      if (typeof value !== "boolean") {
        errors.push({ path, message: "must_be_boolean" });
      }
      return;
    }
    case "object": {
      if (!isPlainObject(value)) {
        errors.push({ path, message: "must_be_object" });
        return;
      }
      // Recurse if the schema has nested `properties`.
      if (schema.properties) {
        const props = schema.properties;
        const required = schema.required ?? [];
        const additional = schema.additionalProperties;
        for (const k of required) {
          if (!(k in (value as Record<string, unknown>))) {
            errors.push({ path: `${path}/${k}`, message: "missing" });
          }
        }
        if (additional === false) {
          for (const k of Object.keys(value as Record<string, unknown>)) {
            if (!(k in props)) {
              errors.push({
                path: `${path}/${k}`,
                message: "unknown_property",
              });
            }
          }
        }
        for (const [k, sub] of Object.entries(props)) {
          if (!(k in (value as Record<string, unknown>))) continue;
          const nested = (value as Record<string, unknown>)[k];
          if (nested === undefined) {
            errors.push({ path: `${path}/${k}`, message: "missing" });
            continue;
          }
          validateAcceptProperty(`${path}/${k}`, nested, sub, errors);
        }
      }
      return;
    }
    case "array": {
      if (!Array.isArray(value)) {
        errors.push({ path, message: "must_be_array" });
        return;
      }
      if (
        typeof schema.minItems === "number" &&
        value.length < schema.minItems
      ) {
        errors.push({ path, message: "too_short" });
      }
      if (
        typeof schema.maxItems === "number" &&
        value.length > schema.maxItems
      ) {
        errors.push({ path, message: "too_long" });
      }
      for (let i = 0; i < value.length; i++) {
        validateAcceptProperty(`${path}/${i}`, value[i], schema.items, errors);
      }
      return;
    }
    default: {
      // Unknown schema branch — fail closed so a YAML drift doesn't
      // silently accept arbitrary values.
      errors.push({ path, message: "unsupported_schema_type" });
    }
  }
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return (
    typeof v === "object" &&
    v !== null &&
    !Array.isArray(v) &&
    Object.getPrototypeOf(v) !== null
  );
}
