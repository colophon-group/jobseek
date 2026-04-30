/**
 * Derive a `{company_name, website}` pair from the single `input` field the
 * legacy request-company UI exposes.
 *
 * The legacy server action accepts free-form text (a name OR a URL); the new
 * `POST /api/web/companies/request` endpoint requires both a non-empty name
 * AND an http(s) URL. Until the UI grows two real fields (post-demo), we
 * derive the pair on the client when it can, and skip the agent-run call
 * otherwise.
 *
 * Returns `null` when input does not parse as an http(s) URL — callers should
 * NOT call `requestAgentRun` in that case (the server would 400).
 */
export interface AgentRunFields {
  company_name: string;
  website: string;
}

export function parseRequestInput(raw: string): AgentRunFields | null {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return null;
  if (!/^https?:\/\//i.test(trimmed)) return null;
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const hostname = url.hostname.replace(/^www\./i, "");
  if (hostname.length === 0) return null;
  return {
    company_name: hostname,
    website: trimmed,
  };
}
