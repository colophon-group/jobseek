/**
 * Blog content layer (#2828).
 *
 * Posts live as MDX files under `apps/web/src/content/blog/<slug>.mdx`
 * with a YAML frontmatter block. Reading happens at render time
 * (server-side); per-post pages prerender at build via
 * `generateStaticParams`, and the index page reads the full set on
 * each ISR regen.
 *
 * Posts are translated per-post via sibling files
 * `<slug>.<locale>.mdx` next to the canonical English `<slug>.mdx`.
 * `getBlogPost(slug, locale)` honors the locale preference and falls
 * back to the canonical source. The sitemap (`lib/sitemap.ts::
 * blogPostEntries`) emits one URL per (post, locale-with-translation)
 * pair, driven by `getBlogPostLocales(slug)`; locales without a
 * translated MDX are skipped so we never advertise duplicate-content
 * English-body URLs under foreign locale paths.
 *
 * Frontmatter contract (validated lightly — missing fields fall back
 * to safe defaults):
 *
 * ```yaml
 * ---
 * title: "..."                       # required
 * description: "..."                 # required (used for meta + index card)
 * datePublished: "2026-05-01"        # required (ISO date)
 * dateModified: "2026-05-07"         # optional; defaults to datePublished
 * author: "Viktor Shcherbakov"       # optional; defaults to siteConfig
 * tags: ["data-analysis"]            # optional
 * relatedCompanies: ["openai", ...]  # optional; sluglist into /company/{slug}
 * relatedWatchlists: ["user/slug"]   # optional; "owner/slug" pairs
 * draft: false                       # optional; true hides from index + sitemap
 * ---
 * ```
 */

import { readFile, readdir, access } from "node:fs/promises";
import { join } from "node:path";
import matter from "gray-matter";
import { type Locale, locales } from "@/lib/i18n";

const BLOG_DIR = join(process.cwd(), "src/content/blog");

export type BlogPostFrontmatter = {
  title: string;
  description: string;
  datePublished: string;
  dateModified: string;
  author: string;
  tags: string[];
  relatedCompanies: string[];
  /** "owner/slug" pairs pointing at /{locale}/{owner}/{slug}. */
  relatedWatchlists: string[];
  draft: boolean;
};

export type BlogPostSummary = BlogPostFrontmatter & {
  slug: string;
};

export type BlogPost = BlogPostSummary & {
  /** Raw MDX body (excluding the frontmatter block). Compile via
   *  `next-mdx-remote/rsc::compileMDX` at the page boundary. */
  body: string;
};

const DEFAULT_AUTHOR = "Viktor Shcherbakov";

function coerceFrontmatter(
  slug: string,
  raw: Record<string, unknown>,
): BlogPostFrontmatter {
  const title = typeof raw.title === "string" ? raw.title.trim() : "";
  const description = typeof raw.description === "string" ? raw.description.trim() : "";
  const datePublished = typeof raw.datePublished === "string" ? raw.datePublished : "";
  if (!title || !description || !datePublished) {
    throw new Error(
      `[blog] post '${slug}' is missing required frontmatter (title / description / datePublished).`,
    );
  }
  const parsedPublished = new Date(datePublished);
  if (Number.isNaN(parsedPublished.getTime())) {
    throw new Error(`[blog] post '${slug}' has invalid datePublished: ${datePublished}`);
  }
  const dateModified = typeof raw.dateModified === "string" && raw.dateModified
    ? raw.dateModified
    : datePublished;
  const parsedModified = new Date(dateModified);
  if (Number.isNaN(parsedModified.getTime())) {
    throw new Error(`[blog] post '${slug}' has invalid dateModified: ${dateModified}`);
  }
  return {
    title,
    description,
    datePublished,
    dateModified,
    author: typeof raw.author === "string" && raw.author ? raw.author : DEFAULT_AUTHOR,
    tags: Array.isArray(raw.tags) ? raw.tags.filter((t): t is string => typeof t === "string") : [],
    relatedCompanies: Array.isArray(raw.relatedCompanies)
      ? raw.relatedCompanies.filter((s): s is string => typeof s === "string")
      : [],
    relatedWatchlists: Array.isArray(raw.relatedWatchlists)
      ? raw.relatedWatchlists.filter((s): s is string => typeof s === "string")
      : [],
    draft: raw.draft === true,
  };
}

async function listBlogFilenames(): Promise<string[]> {
  try {
    const entries = await readdir(BLOG_DIR, { withFileTypes: true });
    return entries
      .filter((e) => e.isFile() && e.name.endsWith(".mdx"))
      .map((e) => e.name);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
}

/**
 * Translation files use the convention `<slug>.<locale>.mdx` next to
 * the canonical English source `<slug>.mdx`. The default-locale source
 * has no locale segment so the slug never collides with a translation.
 *
 * Examples:
 *   welcome-to-the-job-seek-blog.mdx       (en, canonical)
 *   welcome-to-the-job-seek-blog.de.mdx    (de translation)
 *   welcome-to-the-job-seek-blog.fr.mdx    (fr translation)
 *   welcome-to-the-job-seek-blog.it.mdx    (it translation)
 */
function isCanonicalFilename(filename: string): boolean {
  if (!filename.endsWith(".mdx")) return false;
  const stem = filename.slice(0, -".mdx".length);
  // A canonical English file has no `.<locale>` segment before `.mdx`.
  // A translation has `.<locale>` (where locale is one of the
  // supported codes). Anything else is malformed and ignored.
  for (const locale of locales) {
    if (stem.endsWith(`.${locale}`)) return false;
  }
  return true;
}

function slugFromFilename(filename: string): string {
  return filename.replace(/\.mdx$/, "");
}

/**
 * Returns all published posts (canonical English source files only),
 * sorted newest-first by `datePublished`. Drafts are excluded.
 * Translation files (`<slug>.<locale>.mdx`) are not surfaced here —
 * they're per-post locale variants, not separate posts.
 */
export async function listBlogPosts(): Promise<BlogPostSummary[]> {
  const filenames = (await listBlogFilenames()).filter(isCanonicalFilename);
  const posts = await Promise.all(filenames.map(async (filename) => {
    const slug = slugFromFilename(filename);
    const raw = await readFile(join(BLOG_DIR, filename), "utf-8");
    const { data } = matter(raw);
    const fm = coerceFrontmatter(slug, data as Record<string, unknown>);
    return { slug, ...fm };
  }));
  return posts
    .filter((p) => !p.draft)
    .sort((a, b) => b.datePublished.localeCompare(a.datePublished));
}

/**
 * Returns one post by slug, optionally honoring a locale preference.
 *
 * Lookup order:
 *   1. `<slug>.<locale>.mdx` if locale is provided and the file exists
 *   2. `<slug>.mdx` (canonical English) as fallback
 *
 * Returns `null` if neither file exists or if the post is marked
 * `draft`. Throws on malformed frontmatter — that's a build-time
 * mistake we want loud. Frontmatter from the translation file wins
 * over the canonical's; translation files should mirror the canonical
 * shape (same datePublished anchor) and only override translatable
 * fields (title, description, tags). The slug stays canonical.
 */
export async function getBlogPost(
  slug: string,
  locale?: Locale,
): Promise<BlogPost | null> {
  const candidates: string[] = [];
  if (locale && locale !== "en") {
    candidates.push(`${slug}.${locale}.mdx`);
  }
  candidates.push(`${slug}.mdx`);

  for (const filename of candidates) {
    try {
      const raw = await readFile(join(BLOG_DIR, filename), "utf-8");
      const { data, content } = matter(raw);
      const fm = coerceFrontmatter(slug, data as Record<string, unknown>);
      if (fm.draft) return null;
      return { slug, ...fm, body: content };
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
      // try next candidate
    }
  }
  return null;
}

/**
 * Slug list for `generateStaticParams` on the [slug] route. Drafts
 * excluded so they never get a static path.
 */
export async function listBlogSlugs(): Promise<string[]> {
  const posts = await listBlogPosts();
  return posts.map((p) => p.slug);
}

/**
 * Returns the set of locales for which a given canonical post has a
 * translation file on disk (always includes "en" as the canonical
 * source). Used by the sitemap to widen `hreflang` alternates per post
 * — sites without a translation skip that locale rather than emit a
 * duplicate-content English-body URL.
 */
export async function getBlogPostLocales(slug: string): Promise<Locale[]> {
  const present: Locale[] = [];
  for (const locale of locales) {
    const filename = locale === "en" ? `${slug}.mdx` : `${slug}.${locale}.mdx`;
    try {
      await access(join(BLOG_DIR, filename));
      present.push(locale);
    } catch {
      // missing → skip
    }
  }
  return present;
}

/**
 * Reading-time estimate (200 wpm; 1 min minimum). Pure function; the
 * page renders this next to the byline.
 */
export function readingTimeMinutes(body: string): number {
  const words = body.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / 200));
}
