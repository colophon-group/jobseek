import { describe, expect, it } from "vitest";
import { GET as apiGet, HEAD as apiHead } from "../api/route";
import { GET as apiDocsGet, HEAD as apiDocsHead } from "../api-docs/route";
import {
  GET as apiReferenceGet,
  HEAD as apiReferenceHead,
} from "../api-reference/route";
import { GET as developerGet, HEAD as developerHead } from "../developer/route";
import { GET as developersGet, HEAD as developersHead } from "../developers/route";
import { GET as llmsGet, HEAD as llmsHead } from "../llms.txt/route";
import { GET as mcpJsonGet, HEAD as mcpJsonHead } from "../mcp.json/route";
import { GET as openApiJsonGet, HEAD as openApiJsonHead } from "../openapi.json/route";
import { GET as openApiYamlGet, HEAD as openApiYamlHead } from "../openapi.yaml/route";

const notFoundRoutes = [
  ["/api", apiGet, apiHead],
  ["/api-docs", apiDocsGet, apiDocsHead],
  ["/api-reference", apiReferenceGet, apiReferenceHead],
  ["/developer", developerGet, developerHead],
  ["/developers", developersGet, developersHead],
  ["/mcp.json", mcpJsonGet, mcpJsonHead],
  ["/openapi.yaml", openApiYamlGet, openApiYamlHead],
] as const;

const redirectRoutes = [
  ["/llms.txt", "/.well-known/llms.txt", llmsGet, llmsHead],
  ["/openapi.json", "/api/openapi.json", openApiJsonGet, openApiJsonHead],
] as const;

describe("API discovery probe routes", () => {
  it.each(notFoundRoutes)("%s returns a raw 404 for GET and HEAD", async (_, get, head) => {
    const getResponse = await get();
    expect(getResponse.status).toBe(404);
    expect(getResponse.headers.get("content-type")).toBe("text/plain; charset=utf-8");
    expect(getResponse.headers.get("x-robots-tag")).toBe("noindex");

    const headResponse = await head();
    expect(headResponse.status).toBe(404);
    expect(await headResponse.text()).toBe("");
  });

  it.each(redirectRoutes)("%s redirects to its canonical endpoint", async (_, location, get, head) => {
    const getResponse = await get();
    expect(getResponse.status).toBe(308);
    expect(getResponse.headers.get("location")).toBe(location);
    expect(getResponse.headers.get("x-robots-tag")).toBe("noindex");

    const headResponse = await head();
    expect(headResponse.status).toBe(308);
    expect(headResponse.headers.get("location")).toBe(location);
  });
});

