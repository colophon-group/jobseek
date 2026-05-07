import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { compileMDX } from "next-mdx-remote/rsc";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import {
  getBlogPost,
  getBlogPostLocales,
  listBlogSlugs,
  readingTimeMinutes,
  type BlogPost,
} from "@/lib/blog";
import { buildMdxComponents } from "@/components/blog/MdxMentions";
import { RelatedPosts } from "@/components/blog/RelatedPosts";

// Posts are static content authored at PR-merge cadence; ISR window is
// cosmetic. Build-time prerender via `generateStaticParams` covers
// every published post.
export const revalidate = 86400;
// English-only initially; once a post has translated MDX siblings the
// per-post hreflang map can be widened in `generateMetadata`. For now
// only `/en/blog/{slug}` is the canonical surface — the localized
// paths exist (`generateStaticParams` emits all 4 locales for routing
// completeness) but redirect intent flows through the canonical EN URL.
export const dynamicParams = false;

type Props = {
  params: Promise<{ lang: string; slug: string }>;
};

export async function generateStaticParams(): Promise<{ lang: string; slug: string }[]> {
  const slugs = await listBlogSlugs();
  // Emit one (lang, slug) pair per locale so all 4 paths prerender.
  // English is the only locale with real content today; the others
  // serve the same EN body until per-locale MDX siblings exist.
  const locales = ["en", "de", "fr", "it"] as const;
  return slugs.flatMap((slug) => locales.map((lang) => ({ lang, slug })));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang, slug } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const post = await getBlogPost(slug, locale);
  if (!post) return {};

  // Per-post hreflang: only advertise locales that actually have a
  // translated MDX sibling on disk. Mirrors the sitemap's
  // `blogPostEntries` behavior (#2828) — a future EN-only post stays
  // off `<link rel="alternate" hreflang="de" ...>` instead of pointing
  // crawlers at duplicate-content fallbacks (#2849).
  const availableLocales = await getBlogPostLocales(slug);

  return {
    title: post.title,
    description: post.description,
    alternates: buildAlternates(`/blog/${slug}`, locale, availableLocales),
    openGraph: {
      title: post.title,
      description: post.description,
      url: `${siteConfig.url}/${locale}/blog/${slug}`,
      type: "article",
      publishedTime: post.datePublished,
      modifiedTime: post.dateModified,
      authors: [post.author],
    },
  };
}

function buildArticleJsonLd(
  post: BlogPost,
  locale: string,
): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: post.title,
    description: post.description,
    datePublished: post.datePublished,
    dateModified: post.dateModified,
    inLanguage: locale,
    author: {
      "@type": "Person",
      name: post.author,
    },
    publisher: {
      "@type": "Organization",
      name: "Job Seek",
      url: siteConfig.url,
      logo: {
        "@type": "ImageObject",
        url: `${siteConfig.url}${siteConfig.logo.src}`,
      },
    },
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `${siteConfig.url}/${locale}/blog/${post.slug}`,
    },
    keywords: post.tags.length > 0 ? post.tags.join(", ") : undefined,
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

export default async function BlogPostPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  const { slug } = await params;
  const post = await getBlogPost(slug, locale);
  if (!post) notFound();

  const { content } = await compileMDX<Record<string, never>>({
    source: post.body,
    options: { parseFrontmatter: false },
    components: buildMdxComponents(locale),
  });
  const minutes = readingTimeMinutes(post.body);

  const backLabel = i18n._({
    id: "blog.post.backToIndex",
    comment: "Back-to-index link at the top of a blog post (rendered as '← {label}')",
    message: "Blog",
  });
  const readingTimeLabel = i18n._({
    id: "blog.post.readingTime",
    comment: "Reading-time label shown in the blog post byline. {minutes} is the integer minute count.",
    message: "{minutes, plural, one {# min read} other {# min read}}",
    values: { minutes },
  });

  return (
    <>
      <JsonLd data={buildArticleJsonLd(post, locale)} />
      <main className="mx-auto max-w-2xl px-4 py-12">
        <nav className="mb-6 text-sm">
          <Link href={`/${locale}/blog`} className="text-muted hover:underline">
            ← {backLabel}
          </Link>
        </nav>

        <article>
          <header className="mb-8">
            <h1 className="text-3xl font-bold leading-tight">{post.title}</h1>
            <p className="mt-3 text-sm text-muted">
              {formatDate(post.datePublished, locale)} · {post.author} · {readingTimeLabel}
            </p>
            {post.tags.length > 0 && (
              <ul className="mt-4 flex flex-wrap gap-2">
                {post.tags.map((tag) => (
                  <li
                    key={tag}
                    className="rounded-full bg-border-soft px-3 py-1 text-xs text-muted"
                  >
                    {tag}
                  </li>
                ))}
              </ul>
            )}
          </header>

          <div className="blog-post">{content}</div>
        </article>

        <RelatedPosts current={post} locale={locale} />
      </main>
    </>
  );
}
