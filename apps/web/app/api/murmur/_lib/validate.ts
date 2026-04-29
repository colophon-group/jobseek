/**
 * Tiny JSON-Schema-subset validator for Murmur subcommand request bodies.
 *
 * Intentionally narrow: only the constructs used in the YAML schemas at
 * `apps/crawler/murmur/pipelines/add-company.yaml` are recognised. Any
 * unknown construct is treated as a hard validation error — we'd rather
 * reject a body than silently let one through.
 *
 * The validator never throws on an unexpected body shape; instead it
 * returns a list of `SchemaError` objects with JSON-Pointer paths
 * (matching M0's `submit_result` validation-error shape per DESIGN.md
 * §4.2 boundary contracts).
 *
 * @see colophon-group/jobseek#2759
 */

import type { SubcommandSchema, SubcommandPropertySchema } from "./schemas";

/**
 * One per-field error. `path` is a JSON Pointer (`/board_url`,
 * `/per_field/title`); `message` is a stable string token, not prose.
 */
export interface SchemaError {
  readonly path: string;
  readonly message: string;
}

/** Validate an unknown body against an object subcommand schema. */
export function validateBody(
  body: unknown,
  schema: SubcommandSchema,
): SchemaError[] {
  const errors: SchemaError[] = [];
  if (!isPlainObject(body)) {
    errors.push({ path: "", message: "must_be_object" });
    return errors;
  }

  // Required-key check.
  for (const key of schema.required) {
    if (!(key in body)) {
      errors.push({ path: `/${key}`, message: "missing" });
    }
  }

  // additionalProperties: false — any unknown key is rejected.
  for (const key of Object.keys(body)) {
    if (!(key in schema.properties)) {
      errors.push({ path: `/${key}`, message: "unknown_property" });
    }
  }

  // Per-property validation for the keys we know about.
  for (const [key, propSchema] of Object.entries(schema.properties)) {
    if (!(key in body)) continue;
    const value = (body as Record<string, unknown>)[key];
    validateProperty(`/${key}`, value, propSchema, errors);
  }

  return errors;
}

function validateProperty(
  path: string,
  value: unknown,
  schema: SubcommandPropertySchema,
  errors: SchemaError[],
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
      if (schema.pattern && !new RegExp(schema.pattern).test(value)) {
        errors.push({ path, message: "pattern_mismatch" });
      }
      if (schema.format === "uri") {
        // URI: parse-able URL with a non-empty host. Scheme is enforced
        // separately by `pattern: "^https://"` where the YAML wants it.
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
      }
      return;
    }
    default: {
      // Unknown schema type — fail closed.
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
