# SEO and IndexNow

## Active search surface

The acquisition surface is localized home, about, FAQ, blog index, and translated blog articles. With three articles translated into four languages, the sitemap contains **28 URLs** (seven page concepts × four locales). This count grows when articles/translations are published.

| Route family | Policy |
|---|---|
| `/{locale}`, `/about`, `/faq`, `/blog`, `/blog/{slug}` | Indexable; self-canonical; sitemap |
| `/explore`, `/company/{slug}` | `noindex,follow`; excluded from sitemap |
| Account, authentication, checkout, watchlist routes | `noindex,nofollow`; excluded from sitemap |
| `/terms`, `/privacy-policy`, `/license`, `/how-we-index` | `noindex,follow`; excluded from sitemap |
| Legacy `/{locale}/{user}/{watchlist}` | Anonymous 404; owners may redirect to UUID route |

Explore remains an interactive product page. Its filter URLs canonicalize to the queryless locale URL. Its current JavaScript rendering dependency is not an acquisition requirement. Do not reinstate it in the sitemap without revisiting that product decision.

`apps/web/src/lib/sitemap.ts` owns the entry set and XML serializer; `app/sitemap.xml/route.ts` serves it. There are no company/watchlist shards or database reads in sitemap generation. Robots points to `/sitemap.xml`. Public HTML stays crawlable so engines can read noindex; private APIs remain disallowed. Robots is not an access-control mechanism.

Company pages still provide useful product information, company metadata, and share cards. Exclusion is deliberate. Individual jobs have no dedicated indexable detail route; JobPosting rich-result eligibility would require a separate product decision and an expiration policy.

## Localization and blog routes

`buildAlternates` emits canonical and language links, including English `x-default`. Each blog post advertises only its actual translated MDX siblings. Missing translations redirect permanently to the English article; they do not publish English content under a self-canonical foreign-language URL.

`next.config.ts` builds `BLOG_ROUTE_MANIFEST` from repository filenames. Proxy uses this small build-time inventory to return a localized 404 for unknown blog slugs **before streaming**, for both GET and HEAD. It also redirects missing translations. There are no per-request filesystem or search-service lookups. Adding an article or translation requires a new deployment.

Blog pages contain Article JSON-LD and matching article-specific Open Graph/Twitter images. The configured author links to a verified public profile. Other authors must have their identity reviewed before a profile URL is added; do not reuse the default author's URL for them. Shared JSON-LD serialization escapes `<`.

## Content dates

- Update each static sitemap entry in `src/content/config.ts` after substantive visible content changes. Shared footer/navigation changes affect all public pages.
- Update the changed article/translation's `dateModified` with the actual edit date; retain the original publication date.
- Article sitemap entries use each translation's own modification date. A localized blog index uses the latest of its static revision date and its translated articles' dates.
- Do not stamp every request/build with today's date.
- CI runs `.github/scripts/check-seo-content-dates.mjs` against the PR base: changed existing MDX must advance its date (multiple edits on the current UTC day are allowed), and invalid/future dates fail. Correcting old date metadata without changing content is allowed. Static-page dates still require editorial review.

## IndexNow lifecycle

**Automatic path:** `.github/workflows/deploy-web-production.yml` builds and stages the selected main revision, checks it, promotes it, and verifies that `jseek.co` resolves to the promoted deployment. Only then does it run `pnpm --filter @jobseek/web notify-blog-indexnow` from that same checkout.

Every successful web promotion submits current published articles. This intentionally includes unchanged articles: the small idempotent resubmission repairs missed notifications and covers cumulative deployments. A skipped or failed promotion does not submit. The notification step requires `INDEXNOW_KEY` from the `Production` GitHub environment and fails on a missing key, rejected HTTP response, or transport error. A notification failure after promotion does not roll back the site.

**Manual retry:** dispatch `.github/workflows/notify-blog-indexnow.yml` on the deployed revision. It shares production-deploy concurrency and requires the selected checkout SHA to match the deployment currently serving `jseek.co`, using the Vercel API. It never waits for a nonexistent `vercel[bot]` GitHub deployment record. If main is ahead of production, let deployment complete before retrying.

The script loads published MDX and fans out only to each post's actual locales. The common `notifyIndexNow` helper sends to `https://api.indexnow.org/indexnow`, with `keyLocation=https://jseek.co/indexnow-key.txt`; 200/202 are acknowledgements, not proof of indexing. The helper retains safe no-op behavior for non-production callers without a key, but the production script explicitly rejects missing credentials.

### Credentials and verification

- `INDEXNOW_KEY`: same value in Vercel production and GitHub environment `Production`.
- Vercel serves the current proof at `/indexnow-key.txt` with `Cache-Control: no-store`. Verify status without printing the key into logs.
- Automatic submission uses the existing deploy credentials; manual retry additionally reads production identity with `VERCEL_TOKEN` and `VERCEL_ORG_ID`.

```sh
curl -I https://jseek.co/indexnow-key.txt
gh run list --workflow deploy-web-production.yml --branch main --limit 5
gh run list --workflow notify-blog-indexnow.yml --limit 5
```

Watchlist actions no longer submit URLs. Crawler company notification is retired; `apps/crawler/src/indexnow.py` remains legacy reference code, not a scheduled service. Do not restore it while companies remain noindex. Google discovery relies on ordinary crawling and sitemaps; IndexNow does not notify Google.

## Verification and monitoring

The [September 2026 audit](audits/2026-09-28-seo-surface.md) records the original findings and live evidence. Its 32-URL inventory is a historical baseline before Explore was removed.

Check canonical/hreflang and noindex on delivered HTML, all sitemap responses, missing-blog GET/HEAD status, and translation redirects. Unit tests cover metadata, sitemap per-translation dates, robots, Proxy, and the content-date gate. Production-build smoke tests cover account/Explore exclusions and blog status codes.

Search Console and Bing Webmaster Tools are required to determine actual indexed counts, selected canonicals, impressions, and clicks. Separate intentional company/watchlist/Explore removals from problems on marketing/article URLs. Historical coverage counts are not evidence of a current backlink or discovery problem.

`/.well-known/llms.txt` links to product information and original research; `/llms.txt` redirects there. This is discovery hygiene, not a ranking guarantee. Continue publishing useful dated research rather than generating thin indexable filter permutations.
