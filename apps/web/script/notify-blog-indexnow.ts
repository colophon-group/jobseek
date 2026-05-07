/**
 * Submit every published blog post URL to IndexNow (#2843, scope-amended
 * comment: "add notifications of blog posts"). Used as a deploy-time
 * hook from `.github/workflows/notify-blog-indexnow.yml`.
 *
 * Per-post, the post's `getBlogPostLocales(slug)` is the source of
 * truth for which locale variants exist on disk. We submit only those
 * locales — same restriction the sitemap uses (#2828) and the page
 * `<head>` hreflang map uses (#2849), so engines never get pointed
 * at locale variants that fall back to the EN canonical body.
 *
 * Idempotency: re-running on a deploy where no MDX file changed is
 * harmless — IndexNow tolerates re-submission of the same URL
 * (engines dedupe their re-fetch behavior on the receiving side).
 * The only cost is one HTTP POST per run.
 *
 * No-op when `INDEXNOW_KEY` is unset (`notifyIndexNow` short-circuits
 * on missing env). Errors are caught and logged inside
 * `notifyIndexNow`; the script always exits 0 unless a blog read
 * itself errors. Crawlers re-fetch the sitemap on a 1–7 day cadence
 * so a missed submission isn't catastrophic.
 *
 * Run with:
 *   pnpm --filter @jobseek/web exec tsx script/notify-blog-indexnow.ts
 */

import { notifyIndexNow } from "../src/lib/indexnow";
import { listBlogPosts, getBlogPostLocales } from "../src/lib/blog";

async function main(): Promise<void> {
  if (!process.env.INDEXNOW_KEY) {
    console.log("[notify-blog-indexnow] INDEXNOW_KEY unset — exiting no-op");
    return;
  }

  const posts = await listBlogPosts();
  if (posts.length === 0) {
    console.log("[notify-blog-indexnow] no posts found — exiting no-op");
    return;
  }

  // Track per-post failures so we can fail the workflow loudly. The
  // catch inside `notifyIndexNow` swallows network/HTTP errors so
  // existing watchlist `after()` callers stay fire-and-forget — script
  // mode reads the result envelope and turns rejections into a
  // non-zero exit. Without this, a revoked INDEXNOW_KEY or a Bing
  // policy change would silently kill the signal for weeks.
  const failures: string[] = [];
  for (const post of posts) {
    const localesForPost = await getBlogPostLocales(post.slug);
    if (localesForPost.length === 0) {
      console.log(`[notify-blog-indexnow] ${post.slug} has no translation files — skipping`);
      continue;
    }
    const result = await notifyIndexNow([`/blog/${post.slug}`], localesForPost);
    switch (result.kind) {
      case "submitted":
        console.log(
          `[notify-blog-indexnow] submitted /blog/${post.slug} status=${result.status} urls=${result.urlCount} (locales: ${localesForPost.join(", ")})`,
        );
        break;
      case "skipped":
        console.log(
          `[notify-blog-indexnow] /blog/${post.slug} skipped (${result.reason})`,
        );
        break;
      case "rejected":
        failures.push(`/blog/${post.slug} → ${result.status}`);
        break;
      case "errored":
        failures.push(`/blog/${post.slug} → ${String(result.error)}`);
        break;
    }
  }

  if (failures.length > 0) {
    console.error(
      `[notify-blog-indexnow] ${failures.length} failure(s):\n  ${failures.join("\n  ")}`,
    );
    process.exit(1);
  }
  console.log(`[notify-blog-indexnow] done — submitted ${posts.length} post(s)`);
}

main().catch((err) => {
  console.error("[notify-blog-indexnow] failed", err);
  process.exit(1);
});
