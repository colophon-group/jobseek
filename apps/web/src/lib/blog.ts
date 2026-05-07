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
  /**
   * Optional author-curated overrides for the "you may also be
   * interested in" block at the bottom of each post (#2844). Slugs
   * pointing at other posts in this directory; missing/draft slugs are
   * silently dropped at render time. When set, the override list wins
   * over the auto-selection (tag overlap → recency).
   */
  relatedPosts: string[];
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
    relatedPosts: Array.isArray(raw.relatedPosts)
      ? raw.relatedPosts.filter((s): s is string => typeof s === "string")
      : [],
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
 * Returns all published posts (one summary per canonical slug), sorted
 * newest-first by `datePublished`. When `locale` is provided and a
 * translated MDX sibling exists, the translated frontmatter wins for
 * `title`/`description`/`tags`/`author`/`dateModified` — this is what
 * the index page should call so card titles and descriptions render in
 * the user's locale rather than always showing the English canonical.
 *
 * Translation files (`<slug>.<locale>.mdx`) are not surfaced as
 * separate posts; they're per-post locale variants.
 */
export async function listBlogPosts(locale?: Locale): Promise<BlogPostSummary[]> {
  const filenames = (await listBlogFilenames()).filter(isCanonicalFilename);
  const posts = await Promise.all(filenames.map(async (filename) => {
    const slug = slugFromFilename(filename);
    const candidates: string[] = [];
    if (locale && locale !== "en") {
      candidates.push(`${slug}.${locale}.mdx`);
    }
    candidates.push(filename);
    for (const candidate of candidates) {
      try {
        const raw = await readFile(join(BLOG_DIR, candidate), "utf-8");
        const { data } = matter(raw);
        const fm = coerceFrontmatter(slug, data as Record<string, unknown>);
        return { slug, ...fm };
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
      }
    }
    // Unreachable: `filename` is guaranteed to exist (we just listed it),
    // but the type system needs a fallthrough.
    throw new Error(`[blog] post '${slug}' disappeared between listdir and read`);
  }));
  return posts.sort((a, b) => b.datePublished.localeCompare(a.datePublished));
}

/**
 * Returns one post by slug, optionally honoring a locale preference.
 *
 * Lookup order:
 *   1. `<slug>.<locale>.mdx` if locale is provided and the file exists
 *   2. `<slug>.mdx` (canonical English) as fallback
 *
 * Returns `null` if no file matches. Throws on malformed frontmatter
 * — that's a build-time mistake we want loud. Frontmatter from the
 * translation file wins over the canonical's; translation files
 * should mirror the canonical shape (same datePublished anchor) and
 * only override translatable fields (title, description, tags). The
 * slug stays canonical.
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
      return { slug, ...fm, body: content };
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
      // try next candidate
    }
  }
  return null;
}

/**
 * Slug list for `generateStaticParams` on the [slug] route.
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

/**
 * Pick up to `max` posts to surface in the "you may also be interested
 * in" block at the bottom of a post (#2844). Selection is deterministic
 * so the same input always yields the same output across ISR regens.
 *
 * Priority chain:
 *   1. Author-curated `relatedPosts` slugs from frontmatter — kept in
 *      authored order, dropped silently if a slug doesn't resolve to a
 *      published post (typo / draft never landed).
 *   2. Tag-overlap auto-selection — score each candidate by the size of
 *      its tag intersection with the current post; tie-break by
 *      `datePublished` (newest first). Skip candidates with zero tags
 *      or zero overlap.
 *   3. Recency fallback — fill the remainder with the most-recent
 *      published posts, skipping the current one and any already
 *      selected.
 *
 * Empty input (single post in the index, or no other published posts
 * after exclusions) returns an empty array — the caller renders
 * nothing rather than emit a "you may also be interested in" heading
 * with no items.
 */
export function selectRelatedPosts(
  current: BlogPostSummary,
  all: BlogPostSummary[],
  max = 3,
): BlogPostSummary[] {
  const others = all.filter((p) => p.slug !== current.slug);
  const bySlug = new Map(others.map((p) => [p.slug, p]));
  const picked: BlogPostSummary[] = [];

  // 1. Author override.
  for (const slug of current.relatedPosts) {
    const post = bySlug.get(slug);
    if (post && !picked.some((p) => p.slug === post.slug)) {
      picked.push(post);
      if (picked.length >= max) return picked;
    }
  }

  // 2. Tag overlap.
  const currentTags = new Set(current.tags);
  if (currentTags.size > 0) {
    const scored = others
      .filter((p) => !picked.some((q) => q.slug === p.slug))
      .map((p) => ({
        post: p,
        overlap: p.tags.reduce((n, t) => (currentTags.has(t) ? n + 1 : n), 0),
      }))
      .filter(({ overlap }) => overlap > 0)
      .sort((a, b) => {
        if (b.overlap !== a.overlap) return b.overlap - a.overlap;
        return b.post.datePublished.localeCompare(a.post.datePublished);
      });
    for (const { post } of scored) {
      picked.push(post);
      if (picked.length >= max) return picked;
    }
  }

  // 3. Recency fallback.
  const remaining = others
    .filter((p) => !picked.some((q) => q.slug === p.slug))
    .sort((a, b) => b.datePublished.localeCompare(a.datePublished));
  for (const post of remaining) {
    picked.push(post);
    if (picked.length >= max) return picked;
  }

  return picked;
}
