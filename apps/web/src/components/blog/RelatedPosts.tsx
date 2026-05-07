import Link from "next/link";
import { Trans } from "@lingui/react/macro";
import { type Locale } from "@/lib/i18n";
import { listBlogPosts, selectRelatedPosts, type BlogPostSummary } from "@/lib/blog";

/**
 * "You may also be interested in" cross-link block (#2844). Renders
 * 0–3 small cards at the bottom of a post, between the article body
 * and the page footer.
 *
 * Selection logic lives in `lib/blog::selectRelatedPosts` — author-
 * curated `relatedPosts` frontmatter wins, falling back to tag-overlap
 * scoring, then most-recent.
 *
 * The component returns null when there are no other posts to suggest
 * (single-post index, or every other post excluded by filters). Don't
 * surround the call site with a conditional — let the empty-case
 * branch handle itself so the page render stays simple.
 *
 * Locale handling: the component is invoked from the post page where
 * `locale` is already validated. Each card links to
 * `/{locale}/blog/{slug}`, but the card title/description always come
 * from the canonical English summary. Per-locale title rendering for
 * cards belongs in a follow-up that reuses the listBlogPosts(locale)
 * codepath; today the post body itself can already be translated, but
 * the index card data is canonical-only.
 */
export async function RelatedPosts({
  current,
  locale,
}: {
  current: BlogPostSummary;
  locale: Locale;
}) {
  const all = await listBlogPosts();
  const related = selectRelatedPosts(current, all);
  if (related.length === 0) return null;

  return (
    <aside className="mt-12 border-t border-border-soft pt-8">
      <h2 className="mb-4 text-lg font-semibold tracking-tight">
        <Trans
          id="blog.post.relatedPosts.heading"
          comment="Heading above the 'you may also be interested in' cross-link block at the bottom of a blog post"
        >
          You may also be interested in
        </Trans>
      </h2>
      <ul className="grid list-none gap-4 p-0 sm:grid-cols-2 lg:grid-cols-3">
        {related.map((post) => (
          <li key={post.slug}>
            <RelatedPostCard post={post} locale={locale} />
          </li>
        ))}
      </ul>
    </aside>
  );
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

function RelatedPostCard({
  post,
  locale,
}: {
  post: BlogPostSummary;
  locale: Locale;
}) {
  const firstTag = post.tags[0];
  const dateLabel = formatDate(post.datePublished, locale);
  return (
    <Link
      href={`/${locale}/blog/${post.slug}`}
      className="mention flex h-full flex-col gap-2 rounded-md border border-border-soft bg-border-soft/30 p-4 transition-colors hover:bg-border-soft"
    >
      <span className="text-base font-semibold leading-tight">{post.title}</span>
      <span className="line-clamp-3 text-sm text-muted leading-relaxed">
        {post.description}
      </span>
      <div className="mt-auto flex items-center gap-2 pt-2 text-xs text-muted">
        <span>{dateLabel}</span>
        {firstTag && (
          <>
            <span aria-hidden="true">·</span>
            <span className="rounded-full bg-border-soft px-2 py-0.5">
              {firstTag}
            </span>
          </>
        )}
      </div>
    </Link>
  );
}
