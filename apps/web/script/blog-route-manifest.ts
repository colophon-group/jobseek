import { readdirSync } from "node:fs";
import path from "node:path";
import { API_LOCALES } from "@jseek/mcp-server/public-api-contract";

/** Build-time inventory: Proxy must reject missing posts before HTML streams. */
export function buildBlogRouteManifest(webRoot: string): Record<string, string[]> {
  const filenames = new Set(readdirSync(path.join(webRoot, "src/content/blog")));
  const manifest: Record<string, string[]> = {};
  for (const filename of [...filenames].sort()) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*\.mdx$/.test(filename)) continue;
    const slug = filename.slice(0, -4);
    manifest[slug] = API_LOCALES.filter((locale) =>
      filenames.has(locale === "en" ? filename : `${slug}.${locale}.mdx`),
    );
  }
  return manifest;
}
