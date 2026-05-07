import type { Metadata } from "next";
import Link from "next/link";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { listBlogPosts } from "@/lib/blog";

// ISR window. The post list itself is just frontmatter from the file
// system — cheap to regenerate. The 1-day window is cosmetic; deploys
// invalidate the cache anyway, and posts publish via PR-merge cadence
// (not faster than per-deploy).
export const revalidate = 86400;

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({
    id: "blog.meta.title",
    comment: "Document title (<title>) for the blog index page",
    message: "Blog",
  });
  const description = i18n._({
    id: "blog.meta.description",
    comment: "Meta description (under 160 chars) for the blog index page — covers data analyses + report breakdowns + news commentary",
    message: "Data analyses, news, and breakdowns of industry reports — drawn from the postings we monitor across thousands of company career pages.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/blog", locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/blog`,
      type: "website",
    },
  };
}

function formatDate(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleDateString(locale, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export default async function BlogIndexPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  // Pass the locale so per-post translated frontmatter (title /
  // description / tags) wins over the English canonical when a
  // translation sibling exists. Without this the index would always
  // show English titles even on /fr/blog.
  const posts = await listBlogPosts(locale);

  const heading = i18n._({
    id: "blog.index.heading",
    comment: "<h1> on the blog index page",
    message: "Blog",
  });
  const tagline = i18n._({
    id: "blog.index.tagline",
    comment: "One-line tagline under the blog index <h1> — covers data analyses + reports/papers + news",
    message: "Data analyses, news, and breakdowns of industry reports and papers — drawn from the postings we monitor.",
  });
  const empty = i18n._({
    id: "blog.index.empty",
    comment: "Empty-state message shown when no blog posts have been published yet",
    message: "No posts yet — check back soon.",
  });

  return (
    <main className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-3xl font-bold">{heading}</h1>
      <p className="mt-2 text-muted">{tagline}</p>

      {posts.length === 0 ? (
        <p className="mt-8 text-muted">{empty}</p>
      ) : (
        <ul className="mt-8 flex flex-col gap-4">
          {posts.map((post) => (
            <li key={post.slug}>
              {/* Card layout mirrors the in-post `<MentionCard>` (see
                  `src/components/blog/MdxMentions.tsx`) for visual
                  consistency: same border-soft + 30%-opacity body, same
                  rounded-md, same hover state. The chrome paints its
                  own colors so we don't inherit the post-body link
                  underline that lives in `globals.css`'s `.blog-post a`
                  rule. */}
              <Link
                href={`/${locale}/blog/${post.slug}`}
                className="flex flex-col gap-2 rounded-md border border-border-soft bg-border-soft/30 p-5 no-underline transition-colors hover:bg-border-soft"
              >
                <span className="text-xs uppercase tracking-wide text-muted">
                  {formatDate(post.datePublished, locale)} · {post.author}
                </span>
                <h2 className="m-0 text-lg font-semibold leading-tight">
                  {post.title}
                </h2>
                <p className="m-0 text-sm text-muted leading-relaxed">
                  {post.description}
                </p>
                {post.tags.length > 0 && (
                  <ul className="mt-1 flex flex-wrap gap-2">
                    {post.tags.map((tag) => (
                      <li
                        key={tag}
                        className="rounded-full bg-background px-2.5 py-0.5 text-xs text-muted"
                      >
                        {tag}
                      </li>
                    ))}
                  </ul>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
