/**
 * A deliberately lossy serializer for errors from credentialed clients.
 *
 * SDK errors commonly retain request headers, auth config, response bodies,
 * credential-bearing URLs, and nested causes. Logging the raw object (or its
 * message/stack) can therefore disclose secrets. This module emits only a
 * small allowlist of operational fields and never copies arbitrary strings
 * from an error.
 */

export const EXTERNAL_CLIENT_LOG_EVENT = "external_client_error";

const SERVICES = new Set([
  "auth",
  "database",
  "github",
  "indexnow",
  "r2",
  "redis",
  "typesense",
  "external_http",
]);

const SAFE_CODES = new Set([
  "EAI_AGAIN",
  "ECONNABORTED",
  "ECONNREFUSED",
  "ECONNRESET",
  "EPIPE",
  "ENETUNREACH",
  "ENOTFOUND",
  "ETIMEDOUT",
  "ERR_CANCELED",
  "ERR_NETWORK",
  "UND_ERR_CONNECT_TIMEOUT",
]);

const REQUEST_ID_HEADERS = [
  "cf-ray",
  "request-id",
  "traceparent",
  "x-amz-request-id",
  "x-request-id",
] as const;

const SENSITIVE_TEXT = /(api[-_]?key|authorization|bearer|credential|password|secret|session|token)/i;
const SENSITIVE_OPERATION = /(api[-_]?key|authorization|bearer|credential|secret)/i;
const SAFE_REQUEST_ID = /^[A-Za-z0-9._:/=-]{1,128}$/;
const HIGH_ENTROPY_SEGMENT = /^[A-Za-z0-9_-]{24,}$/;

export type ExternalService =
  | "auth"
  | "database"
  | "github"
  | "indexnow"
  | "r2"
  | "redis"
  | "typesense"
  | "external_http";

export type ExternalErrorKind =
  | "auth"
  | "canceled"
  | "invalid_request"
  | "network"
  | "not_found"
  | "rate_limited"
  | "timeout"
  | "unknown"
  | "upstream";

export interface ExternalErrorContext {
  service: ExternalService;
  operation: string;
  retryCount?: number;
}

export interface SafeExternalError {
  event: typeof EXTERNAL_CLIENT_LOG_EVENT;
  service: ExternalService | "unknown";
  operation: string;
  kind: ExternalErrorKind;
  timeout: boolean;
  status?: number;
  code?: string;
  retry_count?: number;
  request_id?: string;
  host?: string;
  path?: string;
}

function safeGet(value: unknown, key: PropertyKey): unknown {
  if ((typeof value !== "object" || value === null) && typeof value !== "function") {
    return undefined;
  }
  try {
    return Reflect.get(value, key);
  } catch {
    return undefined;
  }
}

function errorNodes(error: unknown): unknown[] {
  const nodes: unknown[] = [];
  const queue: unknown[] = [error];
  const seen = new Set<unknown>();

  while (queue.length > 0 && nodes.length < 6) {
    const node = queue.shift();
    if ((typeof node !== "object" || node === null) && typeof node !== "function") continue;
    if (seen.has(node)) continue;
    seen.add(node);
    nodes.push(node);

    for (const key of ["cause", "originalError", "response"] as const) {
      const nested = safeGet(node, key);
      if (nested !== undefined && nested !== node) queue.push(nested);
    }
  }
  return nodes;
}

function firstNumber(nodes: readonly unknown[], keys: readonly string[]): number | undefined {
  for (const node of nodes) {
    for (const key of keys) {
      const candidate = safeGet(node, key);
      if (typeof candidate === "number" && Number.isInteger(candidate)) return candidate;
    }
  }
  return undefined;
}

function extractStatus(nodes: readonly unknown[]): number | undefined {
  const status = firstNumber(nodes, ["httpStatus", "status", "statusCode"]);
  return status !== undefined && status >= 100 && status <= 599 ? status : undefined;
}

function extractCode(nodes: readonly unknown[]): string | undefined {
  for (const node of nodes) {
    const code = safeGet(node, "code");
    if (typeof code === "string" && SAFE_CODES.has(code)) return code;
  }
  return undefined;
}

function headerValue(headers: unknown, name: string): unknown {
  const get = safeGet(headers, "get");
  if (typeof get === "function") {
    try {
      return Reflect.apply(get, headers, [name]);
    } catch {
      return undefined;
    }
  }
  return safeGet(headers, name) ?? safeGet(headers, name.toLowerCase());
}

function extractRequestId(nodes: readonly unknown[]): string | undefined {
  for (const node of nodes) {
    const headers = safeGet(node, "headers");
    for (const name of REQUEST_ID_HEADERS) {
      const value = headerValue(headers, name);
      const candidate = Array.isArray(value) ? value[0] : value;
      if (
        typeof candidate === "string" &&
        SAFE_REQUEST_ID.test(candidate) &&
        !SENSITIVE_TEXT.test(candidate)
      ) {
        return candidate;
      }
    }
  }
  return undefined;
}

function redactUrlPart(value: string): string {
  if (!value || value.length > 64 || SENSITIVE_TEXT.test(value) || HIGH_ENTROPY_SEGMENT.test(value)) {
    return "[redacted]";
  }
  return value.replace(/[^A-Za-z0-9._~-]/g, "_");
}

function sanitizeOperation(value: string): string {
  if (!value || value.length > 256 || SENSITIVE_OPERATION.test(value)) return "unknown";
  const normalized = value
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^[_\-.]+|[_\-.]+$/g, "")
    .slice(0, 64);
  return /^[a-z][a-z0-9_.-]{0,63}$/.test(normalized) ? normalized : "unknown";
}

function sanitizeUrl(rawUrl: unknown, baseUrl?: unknown): { host?: string; path?: string } {
  if (typeof rawUrl !== "string" || rawUrl.length === 0 || rawUrl.length > 2_048) return {};
  try {
    const base = typeof baseUrl === "string" ? baseUrl : "https://external.invalid";
    const parsed = new URL(rawUrl, base);
    const host = parsed.hostname
      .split(".")
      .map(redactUrlPart)
      .join(".");
    const pathSegments = parsed.pathname.split("/").filter(Boolean).map(redactUrlPart);
    return {
      ...(parsed.hostname !== "external.invalid" && host ? { host } : {}),
      path: pathSegments.length > 0 ? `/${pathSegments.join("/")}` : "/",
    };
  } catch {
    return {};
  }
}

function extractUrl(nodes: readonly unknown[]): { host?: string; path?: string } {
  for (const node of nodes) {
    const config = safeGet(node, "config");
    const fromConfig = sanitizeUrl(safeGet(config, "url"), safeGet(config, "baseURL"));
    if (fromConfig.host || fromConfig.path) return fromConfig;

    const direct = sanitizeUrl(safeGet(node, "url"));
    if (direct.host || direct.path) return direct;

    const request = safeGet(node, "request");
    const fromRequest = sanitizeUrl(safeGet(request, "responseURL"));
    if (fromRequest.host || fromRequest.path) return fromRequest;
  }
  return {};
}

function classify(status: number | undefined, code: string | undefined): ExternalErrorKind {
  if (code === "ETIMEDOUT" || code === "ECONNABORTED" || code === "UND_ERR_CONNECT_TIMEOUT") {
    return "timeout";
  }
  if (code === "ERR_CANCELED") return "canceled";
  if (code) return "network";
  if (status === 401 || status === 403) return "auth";
  if (status === 404) return "not_found";
  if (status === 408 || status === 504) return "timeout";
  if (status === 429) return "rate_limited";
  if (status !== undefined && status >= 400 && status < 500) return "invalid_request";
  if (status !== undefined && status >= 500) return "upstream";
  return "unknown";
}

export function safeExternalError(
  error: unknown,
  context: ExternalErrorContext,
): SafeExternalError {
  const nodes = errorNodes(error);
  const status = extractStatus(nodes);
  const code = extractCode(nodes);
  const requestId = extractRequestId(nodes);
  const url = extractUrl(nodes);
  const service = SERVICES.has(context.service) ? context.service : "unknown";
  const operation = sanitizeOperation(context.operation);
  const retryCount = context.retryCount;

  return {
    event: EXTERNAL_CLIENT_LOG_EVENT,
    service,
    operation,
    kind: classify(status, code),
    timeout:
      code === "ETIMEDOUT" ||
      code === "ECONNABORTED" ||
      code === "UND_ERR_CONNECT_TIMEOUT" ||
      status === 408 ||
      status === 504,
    ...(status !== undefined ? { status } : {}),
    ...(code !== undefined ? { code } : {}),
    ...(typeof retryCount === "number" && Number.isInteger(retryCount) && retryCount >= 0
      ? { retry_count: retryCount }
      : {}),
    ...(requestId ? { request_id: requestId } : {}),
    ...url,
  };
}

export function logExternalError(
  level: "error" | "warn",
  context: ExternalErrorContext,
  error: unknown,
): void {
  console[level](EXTERNAL_CLIENT_LOG_EVENT, safeExternalError(error, context));
}
