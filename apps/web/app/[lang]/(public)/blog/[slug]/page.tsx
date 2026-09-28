import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import Link from "next/link";
import { compileMDX } from "next-mdx-remote/rsc";
import remarkGfm from "remark-gfm";
import { cacheLife, cacheTag } from "next/cache";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, ogLocale } from "@/lib/i18n";
import { blogPostCacheTag } from "@/lib/cache-tags";
import { CACHE_TTL_DAY } from "@/lib/cache-ttl";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import {
  getBlogPost,
  getBlogPostLocales,
  listBlogSlugs,
  readingTimeMinutes,
  type BlogPost,
} from "@/lib/blog";
import { buildMdxComponents } from "@/components/blog/MdxMentions";
import { ArticleJumpLink } from "@/components/blog/ArticleJumpLink";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { RelatedPosts } from "@/components/blog/RelatedPosts";

// Posts are static content authored at PR-merge cadence — build-time
// prerender via `generateStaticParams` covers every published post.
// Non-existent slugs fall through to `getBlogPost` returning null and
// `notFound()` firing in the page body. (`dynamicParams = false` would
// be the more direct route, but it's incompatible with cacheComponents.)

type Props = {
  params: Promise<{ lang: string; slug: string }>;
};

export async function generateStaticParams(): Promise<{ lang: string; slug: string }[]> {
  const slugs = await listBlogSlugs();
  const routes = await Promise.all(slugs.map(async (slug) =>
    (await getBlogPostLocales(slug)).map((lang) => ({ lang, slug })),
  ));
  return routes.flat();
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_DAY });
  const { lang, slug } = await params;
  cacheTag(blogPostCacheTag(slug));
  const locale = isLocale(lang) ? lang : defaultLocale;
  const post = await getBlogPost(slug, locale);
  if (!post) return {};

  // Per-post hreflang: only advertise locales that actually have a
  // translated MDX sibling on disk. Mirrors the sitemap's
  // `blogPostEntries` behavior (#2828) — a future EN-only post stays
  // off `<link rel="alternate" hreflang="de" ...>` instead of pointing
  // crawlers at duplicate-content fallbacks (#2849).
  const availableLocales = await getBlogPostLocales(slug);
  // Proxy redirects untranslated URLs before streaming; keep metadata aligned
  // even when invoked directly by a build or client navigation.
  const contentLocale = availableLocales.includes(locale) ? locale : defaultLocale;
  const ogImageUrl = `${siteConfig.url}/og/blog/${contentLocale}/${encodeURIComponent(slug)}`;

  return {
    title: post.title,
    description: post.description,
    twitter: {
      card: "summary_large_image",
      title: post.title,
      description: post.description,
      images: [{ url: ogImageUrl, alt: post.title }],
    },
    alternates: buildAlternates(`/blog/${slug}`, contentLocale, availableLocales),
    // Keep the title/date/author card on a separate route so its
    // Satori/Resvg payload is absent from the ordinary post trace.
    openGraph: {
      title: post.title,
      description: post.description,
      url: `${siteConfig.url}/${contentLocale}/blog/${slug}`,
      type: "article",
      publishedTime: post.datePublished,
      modifiedTime: post.dateModified,
      authors: [post.author],
      locale: ogLocale(contentLocale),
      alternateLocale: availableLocales
        .filter((l) => l !== contentLocale)
        .map((l) => ogLocale(l)),
      images: [{
        url: ogImageUrl,
        width: 1200,
        height: 630,
        alt: post.title,
      }],
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
    image: [`${siteConfig.url}/og/blog/${locale}/${encodeURIComponent(post.slug)}`],
    description: post.description,
    datePublished: post.datePublished,
    dateModified: post.dateModified,
    inLanguage: locale,
    author: {
      "@type": "Person",
      name: post.author,
      url: post.author === siteConfig.creator ? siteConfig.creatorUrl : undefined,
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
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_DAY });
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  const { slug } = await params;
  cacheTag(blogPostCacheTag(slug));
  const availableLocales = await getBlogPostLocales(slug);
  if (availableLocales.length > 0 && !availableLocales.includes(locale)) {
    permanentRedirect(`/${defaultLocale}/blog/${encodeURIComponent(slug)}`);
  }
  const post = await getBlogPost(slug, locale);
  if (!post) notFound();

  const { content } = await compileMDX<Record<string, never>>({
    source: post.body,
    options: {
      parseFrontmatter: false,
      mdxOptions: { remarkPlugins: [remarkGfm] },
    },
    components: {
      ...buildMdxComponents(locale),
      JumpLink: ArticleJumpLink,
      Artwork: ({ assetKey }: { assetKey: string }) => (
        <PublicDomainArt
          asset={publicDomainAssets[assetKey]}
          themeRendering="css-invert"
          sizes="(min-width: 768px) 640px, 100vw"
          className="h-full w-full [&_a]:text-white/90! [&_a]:no-underline!"
        />
      ),
    },
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
      <main
        id="main-content"
        tabIndex={-1}
        className="mx-auto max-w-2xl scroll-mt-12 px-4 py-12"
      >
        <nav className="mb-6 text-sm">
          <Link href={`/${locale}/blog`} className="text-muted hover:underline">
            ← {backLabel}
          </Link>
        </nav>

        <article>
          <header className="mb-8">
            <h1 className="text-3xl font-bold leading-tight">{post.title}</h1>
            <p className="mt-3 text-sm text-muted">
              {formatDate(post.datePublished, locale)} · {post.author === siteConfig.creator
                ? <a href={siteConfig.creatorUrl} className="underline underline-offset-4">{post.author}</a>
                : post.author} · {readingTimeLabel}
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
