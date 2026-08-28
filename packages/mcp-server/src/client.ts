const DEFAULT_BASE = "https://jseek.co";
const INTERNAL_MCP_TOKEN_HEADER = "x-jobseek-internal-mcp-token";

export interface JobseekClientOptions {
  internalMcpToken?: string;
}

export class JobseekClient {
  private baseUrl: string;
  private internalMcpToken: string | undefined;

  constructor(baseUrl = DEFAULT_BASE, options: JobseekClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.internalMcpToken = options.internalMcpToken || undefined;
  }

  private headers(initial?: HeadersInit): Headers {
    const headers = new Headers(initial);
    if (this.internalMcpToken) {
      headers.set(INTERNAL_MCP_TOKEN_HEADER, this.internalMcpToken);
    }
    return headers;
  }

  async get(
    path: string,
    params: Record<string, string | undefined>,
  ): Promise<unknown> {
    const url = new URL(`${this.baseUrl}${path}`);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") {
        url.searchParams.set(k, v);
      }
    }
    const res = this.internalMcpToken
      ? await fetch(url.toString(), { headers: this.headers() })
      : await fetch(url.toString());
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`API error ${res.status}: ${body}`);
    }
    return res.json();
  }

  async post(path: string, body: Record<string, unknown>): Promise<unknown> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`API error ${res.status}: ${text}`);
    }
    return res.json();
  }
}
