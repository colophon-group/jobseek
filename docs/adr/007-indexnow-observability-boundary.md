# ADR-007: IndexNow Observability Boundary

Status: implemented

Date: 2026-07-07

## Context

Company-page IndexNow submission was retired when company pages became
`noindex,follow` and left the sitemap. Active IndexNow paths now live in the
web/blog surfaces:

- watchlist server actions call the web-side notifier for qualifying public
  watchlists;
- the blog workflow submits changed blog URLs after deploy settle time.

The crawler still has a manual legacy `notify-indexnow` command, but there is
no long-running crawler IndexNow container. The active web and blog paths do
not expose Prometheus metrics today; they emit structured logs or GitHub
Actions logs.

## Decision

Do not add a crawler-owned metrics loop for active IndexNow paths. Until the
web app has a durable metrics surface, active IndexNow observability is:

- structured Vercel logs from the web notifier;
- GitHub Actions logs for blog submissions;
- manual crawler command logs only when an operator deliberately runs the
  legacy command.

The broader web metrics gap is tracked separately and should be solved as a web
observability feature rather than by reviving a crawler container for a retired
company-page path.

## Consequences

- IndexNow production checks should query the relevant web deployment logs or
  workflow logs, not crawler Prometheus.
- Crawler deploys should not depend on IndexNow secrets or an IndexNow
  container.
- A future durable dashboard for active IndexNow paths should be implemented
  with the web metrics surface.
- If company-page IndexNow submission is revived, the revival must define its
  own scheduler, metrics, and alerting contract.

## References

- [SEO and IndexNow](../13-seo-and-indexnow.md)
- [`apps/web/src/lib/indexnow.ts`](../../apps/web/src/lib/indexnow.ts)
- [Notify blog IndexNow workflow](../../.github/workflows/notify-blog-indexnow.yml)
- [`apps/crawler/src/indexnow.py`](../../apps/crawler/src/indexnow.py)
