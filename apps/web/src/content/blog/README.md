# Blog content authoring guide

This directory holds MDX source for posts on `https://jseek.co/{locale}/blog/{slug}`. The infrastructure (routes, sitemap, JSON-LD, OG images, Lingui-translated chrome) is in:

- `apps/web/app/[lang]/(public)/blog/page.tsx` — index
- `apps/web/app/[lang]/(public)/blog/[slug]/page.tsx` — post
- `apps/web/app/[lang]/(public)/blog/[slug]/opengraph-image.tsx` — per-post OG card
- `apps/web/src/lib/blog.ts` — frontmatter + body loader
- `apps/web/src/lib/sitemap.ts::blogPostEntries` — sitemap inclusion (English-only canonical)
- `apps/web/src/components/blog/MdxMentions.tsx` — interactive entity-mention components

## Purpose

The blog is jseek's primary growing surface of unique content for SEO + AI-retriever grounding. After the SEO batch (#2821 + #2822 + #2823 part 1) reduced the indexable surface from ~17,000 source-derivable URLs to ~100 URLs of editorial content, the blog is the only place new indexable content gets *created* over time. Every post earns:

- A `/{en}/blog/{slug}` URL that goes into `sitemap.xml`.
- An `Article` JSON-LD block (author, dates, publisher, mainEntityOfPage) — consumable by Google, Bing, Perplexity, ChatGPT, Claude.
- A per-post OG image so social shares don't fall back to the site-wide generic card.
- An entry on the blog index page, ordered by `datePublished`.

## Scope

### What belongs here

The recurring formats (in priority order):

1. **Industry-report breakdowns + jseek-data counterpoint.** Pick a published report (Stack Overflow Developer Survey, WEF Future of Jobs, levels.fyi compensation, Layoffs.fyi follow-up, OECD/Eurostat/SECO labour market, JetBrains State of Developer Ecosystem, GitHub Octoverse, Air Street State of AI). Summarize what it claims fairly. Run the same metric on jseek's data. Show where the measurements agree and diverge. Steelman the source's methodology before contrasting. ~1000 words, 1–2 charts.
2. **Original data analysis on questions only jseek can answer.** Hiring-trajectory follow-ups after announced layoffs, ATS market-share shifts measured at the careers-page level, role-mix changes around major events. ~800–1200 words, methodology footnote required.
3. **News commentary anchored in our data.** When something newsworthy happens (a major restructuring, a regulatory change, a tech-stack shift), respond with what the underlying posting data says — without lapsing into generic op-ed. The data does the talking; commentary is structural.
4. **Process / behind-the-scenes posts.** How the crawler works, how we onboard companies, how we handle WAF-blocked boards. Engaging narrative format — see `how-we-index-job-postings.mdx` as the template.

### What doesn't

- **Product changelog / release notes** — those live elsewhere or as in-app announcements.
- **Pure opinion pieces with no data.** Industry takes are a dime a dozen; our defensibility comes from the dataset.
- **Anything where the source we're "citing" is a single tweet, LinkedIn post, or vendor-marketing page.** Cite primary sources or peer-reviewed reports.
- **SEO-driven keyword-stuffing.** Posts that exist to rank for a query are the pattern Helpful Content / Site Reputation Abuse policies demote. Write for a reader, not a crawler.

## Quality control

### Required for every post

- **Frontmatter is complete and valid.** See the contract below; the loader (`src/lib/blog.ts::coerceFrontmatter`) throws at build time on missing required fields.
- **Methodology footnote** when the post makes any quantitative claim. Scope, time window, inclusion criteria, exclusions, dataset reference. Anchor `#methodology` so it's linkable. The reader should be able to reproduce the result given the same data.
- **Steelmanned sources.** When contrasting against a published report or report author's claim, summarize their position fairly *before* contrasting. The angle is "complementary measurement," not "they're wrong."
- **Charts (if any) are minimal SVG** rendered inline or via a lightweight `<Chart>` component. Never an external chart library shipped in the bundle for one post.
- **Internal links use mention components** for companies / watchlists where they exist (see below). Plain markdown links for anything else.

### Cadence

Quarterly is the floor. Monthly is achievable when aligned with industry-report release calendars. **Empty / half-built blogs trigger Helpful Content demotion** — once the blog is live, commit to a real cadence or pause and document the pause in the index page copy.

### Critic-iteration policy (mandatory for every new post)

Every blog post — including stub posts and minor revisions — must go through a **critic-review cycle before it lands on `main`**. The author writes the post; then three critics read it cold (no access to the author's drafting context) and return feedback; the author iterates; the cycle repeats until critics return clean. The point is to **mitigate the author's context bias** — the assumptions that build up while drafting a post are precisely the ones a fresh reader doesn't share.

The three required perspectives:

- **Cold reader / target-audience freshness.** Reads at normal pace once, then with marker-pen attention. Flags where they got lost (undefined jargon, missing context), where they lost interest (sagging paragraphs, too much detail), what hooked them (concrete details, strong sentences), pacing/structure issues, voice authenticity, audience match, CTA effectiveness, opening/title pull.
- **Independent fact-checker.** Verifies *every* specific technical claim against the actual codebase or cited source. Numbers, behaviors, API patterns, architectural assertions, named entities. The author must explicitly list any **known aspirational claims** in the briefing so the fact-checker doesn't waste cycles re-flagging them; everything else gets verified.
- **Editorial / voice / brand-fit.** Reads against the existing surface tone (FAQ, About, indexing-policy page) and flags voice drift, overused tics (em-dashes, parenthetical asides, colon-then-question constructions, hedge words), sentence-level prose quality, density, structure, headings doing or not doing work, opening/closing strength, and brand fit (does it sound like jseek or like a SaaS startup blog).

Each critic ends with a verdict: SHIP / SHIP-AFTER-EDITS / REWRITE (or PUBLISH / PUBLISH AFTER MINOR EDITS / NEEDS A FULL EDITORIAL PASS / SCRAP for the editorial reviewer). The author iterates until at least the cold-reader and fact-checker return SHIP (no caveats) and the editorial reviewer returns PUBLISH AS-IS or PUBLISH AFTER MINOR EDITS that the author has applied.

The cycle is mandatory because authors uniformly **overrate their own clarity, accuracy, and voice consistency** — the failure mode is silent, not visible, and the cost of an unclear/inaccurate post #1 setting the tone for everything that follows is much higher than the cost of running three reviews. Re-running critics on subsequent revisions is cheap; running them once and shipping isn't enough.

Logistics:
- Critics run as Claude Code agents (or human reviewers in the same lens) and receive only the post text + the briefing — not the author's drafting context.
- Briefing includes pointers to the surface-tone references (FAQ, About, indexing-policy) and the codebase root for the fact-checker.
- Known-aspirational claims must be enumerated in the fact-checker brief so they don't get re-flagged.
- After applying feedback, re-run at least the affected critic. Three rounds is normal; more is fine if the post is high-stakes (e.g. a flagship piece or one that takes a strong position against a published report).

## Frontmatter contract

```yaml
---
title: "..."                        # required, ≤ 70 chars for OG card cleanliness
description: "..."                  # required, ≤ 160 chars (used as meta + index card + OG)
datePublished: "2026-05-07"         # required, ISO date
dateModified: "2026-05-07"          # optional, defaults to datePublished
author: "Viktor Shcherbakov"        # optional, defaults to siteConfig
tags: ["data-analysis"]             # optional, free-form taxonomy
relatedCompanies: ["openai"]        # optional, slug list — informational
relatedWatchlists: ["owner/slug"]   # optional, "owner/slug" pairs — informational
---
```

Currently `relatedCompanies` and `relatedWatchlists` are advisory frontmatter — they're parsed by the loader but the post page renders mentions inline via the MDX components below. If we ever want a "related" sidebar that surfaces every entity touched by a post, the frontmatter is already populated.

## MDX components catalogue

Components are server-rendered, fetch their entity at compile time, and fall back to `<code>{Type slug}</code>` if the entity is missing — so a broken reference is visible during draft review.

### `<Company slug="..." />`

Inline pill linking to `/{locale}/company/{slug}`. Renders the company's icon (or generic building glyph) + name. Useful when discussing an employer in flowing prose:

```mdx
The shift was led by <Company slug="stripe" /> and <Company slug="openai" />.
```

Source: `MdxMentions.tsx::CompanyMention`. Resolves via the ISR-safe `getCompanyBySlug`.

### `<Watchlist owner="..." slug="..." />`

Inline pill linking to `/{locale}/{owner}/{slug}`. Renders a checklist glyph + the watchlist title + `@owner` chip.

```mdx
For the curated set, see <Watchlist owner="colophongroup" slug="big-tech-jobs-in-switzerland" />.
```

Source: `MdxMentions.tsx::WatchlistMention`. Resolves via the ISR-safe `getPublicWatchlistByUserAndSlug`.

### Adding a new mention type

The future-mention candidates flagged in #2828 are `<Job id="..." />`, `<Occupation slug="..." />`, `<Location slug="..." />`, `<Author slug="..." />`. To add one:

1. **Pick an ISR-safe data action.** It must NOT read session/cookies/headers (see `app/__tests__/isr-routes.test.ts::TAINTED_HELPERS`). The blog post page is `revalidate=86400`; tainted helpers would silently break that.
2. **Add a server component to `MdxMentions.tsx`** following the `CompanyMention` shape: `cache()`-wrap the data action, `MentionPill` for the visual, `MissingMention` for the fallback.
3. **Register in `buildMdxComponents()`** with a TitleCase MDX tag.
4. **Document here** with one usage example.
5. **Test rendering** in dev — the live data should resolve, and an intentionally bad reference should fall through to `{Type ...}`.

The `MentionPill` skeleton is shared so a new type inherits the visual treatment for free; only the `icon`, `label`, and (optional) `meta` differ.

## Translation policy

Posts are translated **per-post** rather than blog-wide. The canonical English source lives at `<slug>.mdx`; translations are sibling files at `<slug>.<locale>.mdx` (e.g. `welcome-to-the-job-seek-blog.de.mdx`). The post page (`app/[lang]/(public)/blog/[slug]/page.tsx`) calls `getBlogPost(slug, locale)` which looks up the translated file first and falls back to the canonical English source if it's missing.

The sitemap (`apps/web/src/lib/sitemap.ts::blogPostEntries`) emits one URL per (post, locale) pair **only for locales that have a translated MDX file on disk** — driven by `getBlogPostLocales(slug)`. Locales without a translation are skipped, so we never advertise a duplicate-content English-body URL under a foreign locale path. `x-default` always points at the EN canonical.

To translate a post:

1. Copy `<slug>.mdx` → `<slug>.<locale>.mdx`.
2. Translate the frontmatter (`title`, `description`, `tags`) and body. Keep MDX components (`<Watchlist>`, `<CompanyCard>`, etc.) as-is — they're identifiers, not user-visible strings.
3. Verify the per-locale URL renders (`pnpm dev` → `/{locale}/blog/<slug>`) and the sitemap widens correctly.

The blog page chrome (`<h1>`, "No posts yet" empty state, "min read", "← Blog" nav, etc.) IS translated for de/fr/it via Lingui — see `locales/{de,fr,it}.po` for the `blog.*` and `common.nav.blog` keys. A pre-commit hook (`scripts/check-i18n-coverage.sh`) blocks commits with untranslated chrome strings.

**Known limitations** (tracked as follow-ups):

- `buildAlternates` in `seo.tsx` always emits all 4 locale alternates in the page `<head>`, regardless of which locales have a translated MDX. The sitemap is correct; the page metadata is over-broad. Symptoms only when a future post ships EN-only — fix tracked.

## Local dev workflow

```bash
cd apps/web
pnpm install            # if first time in the worktree
pnpm dev                # starts Next.js at localhost:3000
                        # ensure .env.local exists — DB-backed mention
                        # components throw without DATABASE_URL
```

Author MDX, save, browser hot-reloads.

To verify post landed:

- `curl -s localhost:3000/en/blog | grep "<your title>"`
- `curl -s localhost:3000/en/blog/<your-slug> | grep "@type":"Article"` (JSON-LD)
- Open the post URL — check the OG image route (`/en/blog/<slug>/opengraph-image-...`) renders 1200×630.

## Pre-merge checklist

- [ ] Frontmatter complete; required fields valid.
- [ ] Methodology footnote present (if any quantitative claim).
- [ ] Internal entity links use mention components, not raw markdown links.
- [ ] No external chart-library imports.
- [ ] Reads cleanly end-to-end — narrative arc, not bullet-list dump.
- [ ] Tags align with existing tag vocabulary (`data-analysis`, `infrastructure`, `crawler`, `meta`, etc. — extend deliberately).
- [ ] OG image renders correctly.
- [ ] Article JSON-LD validates in Google Rich Results Test.
- [ ] Lingui translation hook passes (no untranslated chrome strings).
