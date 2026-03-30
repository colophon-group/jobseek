# SEO & AI-SEO Best Practices Report

**Date:** March 2026
**Scope:** Traditional SEO, AI-SEO (GEO/AEO), and Next.js-specific optimization

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Source Registry & Trustworthiness Ratings](#2-source-registry--trustworthiness-ratings)
3. [Traditional SEO Best Practices (2025-2026)](#3-traditional-seo-best-practices-2025-2026)
4. [AI-SEO: Generative Engine Optimization (GEO)](#4-ai-seo-generative-engine-optimization-geo)
5. [AI-SEO: Answer Engine Optimization (AEO)](#5-ai-seo-answer-engine-optimization-aeo)
6. [Optimizing for Specific AI Platforms](#6-optimizing-for-specific-ai-platforms)
7. [Google Algorithm Updates (2025-2026)](#7-google-algorithm-updates-2025-2026)
8. [Zero-Click Search & Featured Snippets](#8-zero-click-search--featured-snippets)
9. [International / Multilingual SEO](#9-international--multilingual-seo)
10. [Next.js-Specific SEO](#10-nextjs-specific-seo)
11. [Key Metrics & Measurement](#11-key-metrics--measurement)
12. [Actionable Recommendations](#12-actionable-recommendations)

---

## 1. Executive Summary

The SEO landscape in 2025-2026 is defined by two converging forces: Google's continued emphasis on helpful, people-first content (E-E-A-T), and the explosive rise of AI-powered search (Google AI Overviews, ChatGPT, Perplexity, Gemini). Traditional SEO remains the foundation — 97% of AI Overview citations come from pages already ranking in the top 20 organic results — but a new discipline, **Generative Engine Optimization (GEO)**, has emerged as essential for visibility in AI-generated answers.

**Key shifts:**
- AI Overviews now appear in 50%+ of Google search results; they reduce clicks by ~58%
- ~60% of all Google searches end with zero clicks
- ChatGPT accounts for 87.4% of all AI referral traffic
- Fewer than 10% of sources cited by ChatGPT/Gemini/Copilot rank in Google's top 10 — GEO requires its own strategy
- E-E-A-T verification became ~27% stricter in 2025; content lacking E-E-A-T signals gets filtered before ranking consideration
- The Princeton/Georgia Tech GEO academic paper demonstrated up to 40% visibility boosts through targeted optimization

---

## 2. Source Registry & Trustworthiness Ratings

Sources are rated **High**, **Medium**, or **Low** based on authority, methodology, and track record.

### Tier 1 — High Trustworthiness

| Source | Type | Why |
|--------|------|-----|
| [Google Search Central](https://developers.google.com/search/docs/essentials) | Official docs | Primary source; Google's own guidelines |
| [Google Search Quality Rater Guidelines (Sept 2025)](http://www.google.com/insidesearch/howsearchworks/assets/searchqualityevaluatorguidelines.pdf) | Official docs | Internal quality framework made public |
| [Google: Creating Helpful, Reliable, People-First Content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) | Official docs | Canonical guidance on content quality |
| [GEO: Generative Engine Optimization — Princeton/Georgia Tech (KDD 2024)](https://arxiv.org/abs/2311.09735) | Academic paper | Peer-reviewed at ACM SIGKDD; introduced GEO framework |
| [Search Engine Land](https://searchengineland.com/) | Industry publication | 15+ year track record, editorial standards, expert contributors (Barry Schwartz, etc.) |
| [Lily Ray (Amsive)](https://lilyraynyc.substack.com/) | Expert analysis | #1 SEO influencer (USA Today 2022), VP SEO Strategy at Amsive, deep E-E-A-T expertise |
| [Moz](https://moz.com/) | Industry research | Pioneer SEO platform, invented Domain Authority metric, rigorous methodology |
| [Ahrefs](https://ahrefs.com/blog/) | Industry research | Largest backlink index (35T links), data-driven studies, respected methodology |
| [Semrush](https://www.semrush.com/) | Industry research | Largest keyword database (25B keywords), publicly traded, regular ranking studies |

### Tier 2 — Medium Trustworthiness

| Source | Type | Why |
|--------|------|-----|
| [First Page Sage](https://firstpagesage.com/seo-blog/) | Industry analysis | Data-driven ranking factor studies; methodology is proprietary but consistent |
| [Backlinko (Brian Dean)](https://backlinko.com/) | Expert blog | Well-known SEO educator; acquired by Semrush; some claims lack primary data |
| [Search Engine Journal](https://www.searchenginejournal.com/) | Industry publication | Established but varies in editorial rigor; good for news, weaker on original research |
| [WordStream](https://www.wordstream.com/blog/) | Industry blog | Good practical guides; owned by LocaliQ; primarily PPC-focused |
| [Conductor](https://www.conductor.com/) | Industry research | Enterprise SEO platform; publishes AEO/GEO benchmarks; vendor bias possible |
| [Next.js Official Docs](https://nextjs.org/docs/) | Official docs | Authoritative for Next.js but not for SEO strategy |
| [Yext](https://www.yext.com/) | Industry research | AI visibility studies; some vendor bias but solid data access |
| [Tryprofound](https://www.tryprofound.com/) | Industry research | AI citation pattern research; newer source, methodology appears sound |

### Tier 3 — Lower Trustworthiness (use with caution)

| Source | Type | Why |
|--------|------|-----|
| Various agency blogs (HOTH, Svitla, enfuse-solutions, etc.) | Marketing content | Often derivative; may overstate claims for lead generation; thin original research |
| Medium articles, LinkedIn posts | Individual opinions | Unreviewed; useful for anecdotes but not reliable for data claims |
| Unnamed "studies" or statistics without source | Unsourced claims | Common in SEO content; treat with skepticism unless primary source is verifiable |

---

## 3. Traditional SEO Best Practices (2025-2026)

### 3.1 Content Quality & E-E-A-T

E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) is now the dominant quality framework. While not a direct ranking factor, it functions as an **AI filtering mechanism** — content lacking clear E-E-A-T signals gets filtered before ranking consideration.

**Best practices:**

- **Experience**: Demonstrate firsthand, real-world knowledge. Google looks for evidence that the author has actually done what they're writing about — not theory or secondhand summaries.
- **Expertise**: Show deep subject-matter knowledge. Author bios, credentials, and bylines matter. Link to authoritative references.
- **Authoritativeness**: Build topical authority through comprehensive coverage of your domain. Hub-and-spoke content architecture helps prove expertise to algorithms.
- **Trustworthiness**: HTTPS, clear contact information, transparent sourcing, privacy policies, factual accuracy. This is the center of E-E-A-T — all other elements feed into trust.

**For YMYL (Your Money or Your Life) topics:** Health, finance, legal, and safety content is held to higher E-E-A-T standards, but the framework applies to all content.

Sources: [Google Search Quality Rater Guidelines (Sept 2025)](http://www.google.com/insidesearch/howsearchworks/assets/searchqualityevaluatorguidelines.pdf), [Google: Creating Helpful Content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content), [Keywords Everywhere E-E-A-T Guide](https://keywordseverywhere.com/blog/google-e-e-a-t-guidelines-an-overview/)

### 3.2 Ranking Factors Hierarchy (2025-2026)

Based on First Page Sage's data-driven analysis and cross-referenced with industry consensus:

1. **Consistent publication of high-quality, satisfying content** — The #1 factor. Google rewards consistent producers of helpful information with faster indexing and higher rankings.
2. **Keywords in meta title & throughout content** — Proper semantic keyword usage, not stuffing. Title tags: 50-60 characters, primary keyword near the beginning.
3. **Backlinks** — Still the #3 factor (~13% weight) but declining as Google's AI becomes better at evaluating content quality independently.
4. **User engagement signals** — Dwell time, bounce rate, click-through rate. Creating the most fulfilling response to the searcher's intent should be the primary focus.
5. **Technical SEO** — Crawlability, indexability, Core Web Vitals, structured data.
6. **E-E-A-T signals** — Author credentials, site reputation, topical authority.
7. **Freshness** — Content recency matters, especially for time-sensitive queries.

Sources: [First Page Sage Ranking Factors 2025](https://firstpagesage.com/seo-blog/the-google-algorithm-ranking-factors/), [WordStream Ranking Factors 2025](https://www.wordstream.com/blog/seo-ranking-factors-2025)

### 3.3 Technical SEO

#### Core Web Vitals

The three metrics (all must pass simultaneously):

| Metric | What it Measures | Good Threshold | Notes |
|--------|-----------------|----------------|-------|
| **LCP** (Largest Contentful Paint) | Loading performance | < 2.5 seconds | Largest visible element render time |
| **INP** (Interaction to Next Paint) | Responsiveness | < 200ms | Replaced FID in March 2024; measures all interactions |
| **CLS** (Cumulative Layout Shift) | Visual stability | < 0.1 | Elements staying in place during load |

**Key stats:** Only 47-54% of websites meet all CWV thresholds. Sites improving from "Poor" to "Good" report 25% conversion rate increases and 35% bounce rate reductions. Sites with poor INP (>300ms) saw 31% more traffic drops in Google's December 2025 update.

**Optimization priorities:**
- Server response time (TTFB) under 200ms — use edge computing (Cloudflare Workers, Vercel Edge Functions, AWS Lambda@Edge)
- CDN for static assets
- Image optimization (WebP/AVIF, lazy loading, responsive sizes)
- Minimize JavaScript blocking
- Font loading strategies (font-display: swap, preloading)
- Avoid layout shifts from dynamic content/ads

#### Crawlability & Indexability

- Clean site architecture with logical hierarchy
- XML sitemaps (keep updated, submit via Search Console)
- robots.txt properly configured (don't block CSS/JS needed for rendering)
- Canonical URLs to prevent duplicate content issues
- Internal linking with descriptive anchor text
- Mobile-first design (Google's primary index is mobile)

#### Structured Data / Schema Markup

Google recommends **JSON-LD** as the preferred format. Key schema types:

- `Organization` — brand identity
- `WebSite` with `SearchAction` — sitelinks search box
- `Article` / `BlogPosting` — content pages
- `JobPosting` — job listings (highly relevant for Jobseek)
- `BreadcrumbList` — navigation structure
- `FAQ` — question/answer content
- `Product` — product pages
- `LocalBusiness` — local presence

**Why it matters in 2026:** Structured data's primary value is shifting from rich snippets to AI visibility. ChatGPT, Perplexity, and Google AI Overviews parse JSON-LD when crawling pages. Well-implemented structured data helps AI systems understand, trust, and cite your content.

Sites with rich results see 20-30% higher click-through rates. Pages with FAQ schema are 60% more likely to be featured in AI Overviews.

Sources: [Google Structured Data Docs](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data), [digidop.com Structured Data & GEO](https://www.digidop.com/blog/structured-data-secret-weapon-seo), [SEO Strategy JSON-LD Guide](https://www.seostrategy.co.uk/schema-structured-data/json-ld-guide/)

### 3.4 On-Page SEO

- **Title tags**: 50-60 characters, primary keyword near the beginning
- **Meta descriptions**: 150-160 characters, compelling copy with call-to-action
- **Header hierarchy**: Single H1, logical H2-H6 structure
- **Internal linking**: Descriptive anchor text, hub-and-spoke topic clusters
- **URL structure**: Short, descriptive, keyword-inclusive
- **Image optimization**: Descriptive alt text, compressed formats, lazy loading
- **Content depth**: Go deep on topics rather than surface-level coverage
- **User intent matching**: Content must satisfy the searcher's actual need (informational, navigational, transactional, commercial)

### 3.5 Off-Page SEO

- **Quality backlinks** from authoritative, topically-relevant sites
- **Digital PR**: Original research, data studies, expert commentary for journalists
- **Unlinked brand mentions**: Monitor and convert to backlinks
- **Guest contributions** to authoritative industry publications
- **Brand signals**: Consistent NAP (Name, Address, Phone), social profiles, knowledge panel presence

---

## 4. AI-SEO: Generative Engine Optimization (GEO)

### 4.1 What is GEO?

GEO (Generative Engine Optimization) is the practice of making your brand discoverable and favorable in AI-generated search results and responses. Unlike traditional SEO (ranking in SERPs to earn clicks), GEO aims to position your content as the **primary source AI engines reference** when generating answers.

### 4.2 The Academic Foundation

The Princeton/Georgia Tech/AI2/IIT Delhi research team published the foundational GEO paper at ACM SIGKDD 2024:

**Key findings:**
- GEO can boost visibility in generative engine responses by up to **40%**
- Efficacy varies across domains — domain-specific optimization methods are needed
- The researchers created **GEO-bench**, a large-scale benchmark of diverse user queries
- This is a black-box optimization framework (you can't see inside the AI, but you can optimize inputs)

**Optimization methods that worked best in the study:**
1. Adding authoritative citations and statistics
2. Including quotations from recognized experts
3. Using technical terminology appropriate to the domain
4. Structuring content with clear, direct answers
5. Providing unique, specific information not found elsewhere

Source: [GEO Paper (arXiv)](https://arxiv.org/abs/2311.09735), [KDD 2024 Proceedings](https://dl.acm.org/doi/10.1145/3637528.3671900)

### 4.3 Market Reality (2025-2026)

- **50% of consumers** use AI-powered search as their primary method (McKinsey, Oct 2025)
- Most enterprise marketing teams have a GEO initiative by early 2026; most SMBs have not started
- **Only 16% of brands** systematically track AI search performance (as of Sept 2025)
- GEO and traditional SEO overlap but are not identical — fewer than 10% of sources cited by ChatGPT/Gemini/Copilot rank in Google's top 10

### 4.4 Core GEO Strategies

1. **Structure content for AI consumption**
   - Clear headings that match likely queries
   - Direct answers in the first 1-2 sentences of each section (44.2% of LLM citations come from the first 30% of text)
   - Bullet points, numbered lists, comparison tables
   - FAQ sections with concise 50-100 word answers

2. **Build topical authority**
   - Hub-and-spoke content architecture
   - Comprehensive topic clusters
   - Consistent publication cadence

3. **Demonstrate credibility**
   - Cite authoritative sources within your content
   - Include statistics, data, and named expert quotes
   - Author bios with verifiable credentials
   - E-E-A-T signals throughout

4. **Schema markup for AI**
   - JSON-LD structured data (Organization, Article, FAQ, etc.)
   - AI systems parse structured data when crawling — this directly aids citation

5. **Cross-platform presence (consensus signals)**
   - AI platforms scan for agreement across multiple independent sources
   - Consistent brand positioning across your site, Reddit, YouTube, industry publications, review sites (G2, etc.)
   - This "consensus signal" is what triggers AI citations

6. **Content freshness**
   - Regular updates to key content
   - AI platforms favor recency signals
   - Timestamped content with clear publication dates

Sources: [Search Engine Land — GEO Guide](https://searchengineland.com/mastering-generative-engine-optimization-in-2026-full-guide-469142), [First Page Sage — GEO Best Practices](https://firstpagesage.com/seo-blog/generative-engine-optimization-best-practices/), [WordStream — GEO vs SEO](https://www.wordstream.com/blog/generative-engine-optimization)

---

## 5. AI-SEO: Answer Engine Optimization (AEO)

### 5.1 What is AEO?

AEO is the practice of structuring pages so AI-powered answer engines (Google AI Overviews, ChatGPT, Perplexity, Copilot) can extract, cite, and attribute your brand as a trusted source. Success is measured not by clicks, but by **citations** — instances where your domain appears as a trusted source within an AI answer.

### 5.2 AEO vs GEO

- **GEO** is the broader discipline (optimizing for generative AI search in general)
- **AEO** focuses specifically on being selected as *the* answer — the cited source in a generated response
- Both emphasize structure, authority, and trust; AEO is more granular about answer formatting

### 5.3 Core AEO Strategies

1. **Direct answer format**: Lead each section with a 50-100 word direct answer to the heading question
2. **Structured content**: Clear headings, bullet points, tables, numbered lists — AI models thrive on well-organized information
3. **Schema markup**: FAQ, HowTo, and Q&A schemas increase extraction probability
4. **Topic clusters**: Build comprehensive authority on specific subjects
5. **E-E-A-T compliance**: Author credentials, sourcing, factual accuracy
6. **Concise, quotable passages**: Write sentences that can stand alone as extracted answers

### 5.4 Key Statistics

- AI Overviews jumped from ~6.5% of queries (Jan 2025) to 13.1% (Mar 2025) — trajectory is steeply upward (Semrush)
- Question-based queries trigger AI Overviews 99.2% of the time
- ChatGPT accounts for 87.4% of all AI referral traffic
- Pages with FAQ schema are 60% more likely to be featured in AI Overviews

Sources: [Conductor — AEO Guide](https://www.conductor.com/academy/answer-engine-optimization/), [LLMrefs — AEO Complete Guide](https://llmrefs.com/answer-engine-optimization), [Tryprofound — AEO Playbook](https://www.tryprofound.com/resources/articles/answer-engine-optimization-aeo-guide-for-marketers-2025)

---

## 6. Optimizing for Specific AI Platforms

### 6.1 Platform Differences

Each AI platform has distinct citation behaviors — there is **very little overlap** in what each model cites:

| Platform | Citation Style | What It Trusts | Traffic Model |
|----------|---------------|----------------|---------------|
| **Google AI Overviews** | In-SERP attribution | Top-ranking pages (97% from top 20), schema markup, E-E-A-T | Reduced CTR (~58% fewer clicks) |
| **ChatGPT** | Training data (mostly static) | Internet consensus; agreement across multiple sources | Low direct traffic; brand awareness |
| **Perplexity** | Real-time web search, clickable citations | Industry experts, customer reviews, recent content | Highest direct traffic potential |
| **Gemini** | Brand's own content | What the brand itself says; first-party sources | Growing (30% MAU surge in Q4 2025) |
| **Copilot (Bing)** | Bing index + web search | Bing-indexed pages, structured content | Moderate |

### 6.2 Cross-Platform Strategy

- **Front-load answers**: 44.2% of all LLM citations are pulled from the first 30% of the text
- **Build consensus**: Consistent positioning across your site, Reddit, YouTube, industry pubs, review sites
- **Monitor actively**: Query each platform with your 10-20 highest-value keywords weekly
- **Video presence**: YouTube tutorials and thought leadership content are increasingly weighted by AI systems
- **Regular content updates**: Maintain recency signals that AI platforms favor

### 6.3 Expert Perspective (Lily Ray)

Lily Ray, the top-ranked SEO expert, advises:
- Cut all traffic projections in half for 2026 due to ChatGPT impact
- Stop relying on synthetic AI rank tracking; focus on first-party log files for real user data
- The era of optimizing exclusively for search rankings is ending — optimize for visibility across all AI surfaces

Sources: [Tryprofound — AI Citation Patterns](https://www.tryprofound.com/blog/ai-platform-citation-patterns), [Sapt — AI Search Optimization Guide](https://sapt.ai/insights/ai-search-optimization-complete-guide-chatgpt-perplexity-citations), [Lily Ray — Reflection on SEO & AI Search 2025](https://lilyraynyc.substack.com/p/a-reflection-on-seo-and-ai-search)

---

## 7. Google Algorithm Updates (2025-2026)

### 7.1 2025 Updates

Google launched **4 confirmed algorithmic updates** in 2025:

| Update | Dates | Focus |
|--------|-------|-------|
| **March 2025 Core Update** | Mar 13–27 | Helpful content quality |
| **June 2025 Core Update** | Jun 30–Jul 17 | High-quality, helpful content across industries |
| **August 2025 Spam Update** | Aug 26–Sep 22 | Targeting spam tactics, thin content |
| **December 2025 Core Update** | Dec 11–29 | E-E-A-T, Core Web Vitals; sites with poor INP (>300ms) saw 31% more drops |

### 7.2 2026 Updates (so far)

- **February 2026 Discover Core Update** — began Feb 5 for US English

### 7.3 Consistent Themes Across Updates

- Helpful, people-first content is the primary signal
- AI-generated content is acceptable if it serves users — but unedited AI content without E-E-A-T signals is penalized
- User engagement and satisfaction metrics are gaining weight
- Sites with strong topical authority are rewarded
- Thin, derivative, and purely SEO-driven content is devalued

Sources: [Search Engine Land — 2025 Updates Review](https://searchengineland.com/google-algorithm-updates-2025-in-review-3-core-updates-and-1-spam-update-466450), [Search Engine Journal — Algorithm History](https://www.searchenginejournal.com/google-algorithm-history/), [GSQI — December 2025 Analysis](https://www.gsqi.com/marketing-blog/google-december-2025-broad-core-update-analysis-findings/)

---

## 8. Zero-Click Search & Featured Snippets

### 8.1 The Scale of Zero-Click

- **58.5% of US searches** and **59.7% of EU searches** end without a click (Semrush 2025)
- AI Overviews further reduce clicks by ~58% when they appear
- Featured snippets sit at "Position Zero" above organic listings

### 8.2 Optimization Strategies

1. **Target question-based queries** — these trigger AI Overviews 99.2% of the time
2. **Answer in 40-60 words** — the sweet spot for featured snippet extraction
3. **Use structured formats**: lists, tables, step-by-step instructions
4. **Hub-and-spoke content strategy** — proves topical authority to Google
5. **Optimize for video snippets** — YouTube videos answering queries in the first 30 seconds now compete for snippet real estate
6. **Complete Google Business Profile** — for local zero-click results

### 8.3 Brand Visibility Without Clicks

Zero-click doesn't mean zero value. Strategies for capturing value:
- Build brand recognition through consistent SERP presence
- Use featured snippets as brand awareness touchpoints
- Optimize for People Also Ask (PAA) boxes
- Maintain Knowledge Panel accuracy
- Track impressions and brand searches, not just clicks

Sources: [Semrush zero-click study 2025](https://click-vision.com/zero-click-search-statistics), [niumatrix — Zero-Click Guide 2026](https://niumatrix.com/zero-click-search-optimization/)

---

## 9. International / Multilingual SEO

### 9.1 Critical Implementation Issues

**75% of websites targeting international audiences have hreflang implementation errors** that fragment their rankings. Getting the fundamentals right puts you ahead of most competitors.

### 9.2 Best Practices

1. **URL structure**: Subdirectories (`/de/`, `/fr/`) preferred for SEO authority consolidation (vs. subdomains or ccTLDs)
2. **Hreflang implementation**:
   - Self-referencing hreflang on every page
   - Symmetric/reciprocal annotations (every page must point back)
   - Valid ISO 639-1 language codes + ISO 3166-1 Alpha-2 country codes
   - Absolute URLs (not relative)
   - `x-default` for fallback/language selector pages
3. **Content quality**: Google detects machine-translated content; unedited machine translation triggers low-quality penalties across ALL language versions
4. **Localization > Translation**: Search engines reward content that feels "native" rather than "translated"
5. **Canonical tags**: Coordinate with hreflang; Google treats hreflang as hints, and canonical tags can override them
6. **Per-locale keyword research**: Search terms differ across languages even for the same concept

### 9.3 Google's Position (May 2025)

Google reiterated that hreflang signals are treated as **hints**, not directives. Canonical tags, site structure, content similarity, and indexation status can influence which version gets shown.

Sources: [Google — Managing Multi-Regional Sites](https://developers.google.com/search/docs/specialty/international/managing-multi-regional-sites), [Search Engine Land — International SEO Guide](https://searchengineland.com/guide/international-seo), [digitalapplied — International SEO 2026](https://www.digitalapplied.com/blog/international-seo-2026-hreflang-multilingual-guide)

---

## 10. Next.js-Specific SEO

*All information in this section sourced from official Next.js documentation (nextjs.org), last updated March 2026. Trustworthiness: **High** — primary source.*

### 10.1 Metadata API (App Router)

#### Static Metadata
Export a `Metadata` object from any `layout.tsx` or `page.tsx` (Server Components only):

```ts
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'My Blog',
  description: 'A blog about...',
}
```

#### Dynamic Metadata with `generateMetadata`
For data-dependent metadata, export an async function:

```ts
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const product = await fetch(`https://.../${(await params).id}`).then(r => r.json())
  return { title: product.title, description: product.description }
}
```

Use React `cache()` to avoid duplicate data fetches between `generateMetadata` and the page component.

#### Key Metadata Fields

- **`title`**: Supports `string`, `template` (`'%s | Jobseek'`), `default`, and `absolute` variants. Templates apply to child segments, not the defining segment.
- **`description`**: Standard meta description.
- **`metadataBase`**: Set once in root layout (e.g., `new URL('https://jobseek.example.com')`) — foundation for all URL-based metadata.
- **`openGraph`**: Full support for `title`, `description`, `url`, `siteName`, `images`, `locale`, `type`.
- **`twitter`**: Card types (`summary_large_image`, etc.), creator, images.
- **`robots`**: Per-page `index`/`follow`/`noindex`/`nofollow`, `googleBot` directives.
- **`alternates`**: Canonical URLs and hreflang language alternates.
- **`verification`**: Google, Yandex, Yahoo site verification codes.

#### Metadata Merging
Child metadata **shallowly overwrites** parent metadata. If a child defines `openGraph`, it replaces the parent's `openGraph` entirely. Share common fields via a shared variable.

#### Dynamic OG Images
Create `opengraph-image.tsx` using `ImageResponse` from `next/og`:

```tsx
import { ImageResponse } from 'next/og'
export default async function Image({ params }) {
  const post = await getPost(params.slug)
  return new ImageResponse(<div style={{...}}>{post.title}</div>)
}
```

#### Streaming Metadata (v15.2+)
For dynamically rendered pages, `generateMetadata` can stream — UI renders first, metadata tags are appended once resolved. Automatically disabled for HTML-limited bots (e.g., `facebookexternalhit`, `Twitterbot`).

### 10.2 Rendering Strategies & SEO Impact

| Strategy | Description | SEO Impact |
|----------|-------------|------------|
| **SSG (Static)** | Pre-rendered at build time | Best: fastest TTFB, fully crawlable |
| **SSR (Dynamic)** | Rendered per-request on server | Good: content always fresh, fully crawlable |
| **ISR** | Static with background revalidation | Great balance: fast TTFB + fresh content |
| **PPR (Partial Prerendering)** | Static shell + streamed dynamic parts | Advanced: static shell is immediately crawlable |
| **CSR (Client-only)** | Rendered in browser after hydration | **Poor for SEO**: crawlers may miss content |

**Best practice**: Use `generateStaticParams` for pages with known URLs. Keep SEO-critical content in Server Components. Push `'use client'` to leaf interactive components only.

### 10.3 Sitemap Generation

#### Dynamic Sitemap (`app/sitemap.ts`)

```ts
import type { MetadataRoute } from 'next'

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: 'https://acme.com', lastModified: new Date(), changeFrequency: 'yearly', priority: 1 },
    { url: 'https://acme.com/about', lastModified: new Date(), changeFrequency: 'monthly', priority: 0.8 },
  ]
}
```

#### Localized Sitemap
Add `alternates.languages` for hreflang in sitemaps:

```ts
{
  url: 'https://acme.com',
  alternates: { languages: { es: 'https://acme.com/es', de: 'https://acme.com/de' } },
}
```

#### Multiple Sitemaps (Large Sites)
Use `generateSitemaps` to split beyond Google's 50,000 URL limit per sitemap.

### 10.4 Robots.txt (`app/robots.ts`)

```ts
import type { MetadataRoute } from 'next'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: 'Googlebot', allow: ['/'], disallow: '/private/' },
      { userAgent: ['Applebot', 'Bingbot'], disallow: ['/'] },
    ],
    sitemap: 'https://acme.com/sitemap.xml',
  }
}
```

Block admin/API/auth routes: `/api/`, `/dashboard/`, `/(auth)/`. Always include the sitemap URL.

### 10.5 Internationalized SEO in Next.js

#### Locale-Based Routing
Use sub-path routing via `[lang]` dynamic segment with middleware for locale detection.

#### hreflang in Metadata
```ts
export const metadata = {
  metadataBase: new URL('https://acme.com'),
  alternates: {
    canonical: '/',
    languages: { 'en-US': '/en-US', 'de-DE': '/de-DE' },
  },
}
```

#### Static Generation for Locales
```ts
export async function generateStaticParams() {
  return [{ lang: 'en' }, { lang: 'de' }, { lang: 'fr' }, { lang: 'it' }]
}
```

Set `<html lang={locale}>` dynamically in the root layout. Include `x-default` hreflang. Each locale needs a unique canonical URL. Server Components handle translations without increasing client JS bundle.

### 10.6 Structured Data (JSON-LD)

```tsx
export default async function Page({ params }) {
  const product = await getProduct((await params).id)
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'JobPosting',
    title: product.name,
    description: product.description,
  }
  return (
    <section>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c'),
        }}
      />
      {/* page content */}
    </section>
  )
}
```

**Key points:**
- Use native `<script>`, not `next/script` — JSON-LD is data, not executable code
- **XSS prevention**: Replace `<` with `\u003c` in stringified output
- **TypeScript typing**: Use `schema-dts` package for type-safe schema (`WithContext<JobPosting>`)
- **Validate** with [Google Rich Results Test](https://search.google.com/test/rich-results) or [Schema Markup Validator](https://validator.schema.org/)
- **Key types for Jobseek**: `JobPosting`, `Organization`, `WebSite`, `BreadcrumbList`, `SearchAction`

### 10.7 Canonical URLs

```ts
export const metadata: Metadata = {
  metadataBase: new URL('https://acme.com'),
  alternates: { canonical: '/products/widget' },
}
```

Each page needs a unique canonical. For locale variants, each locale gets its own canonical plus hreflang alternates. Route groups `(folder)` don't affect URLs, so no duplicate content issues. Configure `trailingSlash` in `next.config.ts` for consistency.

### 10.8 Performance & Core Web Vitals

#### Image Optimization (`next/image`)
- Automatic WebP/AVIF format conversion
- Lazy loading by default; use `loading="eager"` or `preload` prop for LCP images
- `width`/`height` required to prevent CLS; use `fill` + `sizes` for responsive layouts
- `placeholder="blur"` for perceived performance

#### Font Optimization
- `next/font` self-hosts and pre-loads fonts for zero CLS (but requires SWC)
- **For this project** (Lingui Babel plugin): Use `@font-face` in CSS with `font-display: swap` and `size-adjust`

#### Script Optimization (`next/script`)
- `beforeInteractive`: consent managers, bot detection only
- `afterInteractive` (default): analytics, tag managers
- `lazyOnload`: chat widgets, social embeds — best for non-critical scripts
- `worker` (experimental): offloads to web worker

#### Code Splitting
- Automatic per-route in App Router
- Server Components reduce client JS by keeping non-interactive logic server-side
- `next/dynamic` for heavy client components: `dynamic(() => import('./Chart'), { ssr: false })`

### 10.9 Implementation Checklist

- [ ] `metadataBase` in root layout
- [ ] `title.template` (e.g., `'%s | Jobseek'`)
- [ ] Unique `title` + `description` per page
- [ ] Canonical URLs on every page (`alternates.canonical`)
- [ ] `app/sitemap.ts` with localized alternates
- [ ] `app/robots.ts` blocking private routes
- [ ] JSON-LD structured data per page type (with XSS sanitization)
- [ ] Open Graph + Twitter card metadata
- [ ] Dynamic OG images (`opengraph-image.tsx`)
- [ ] `hreflang` annotations via `alternates.languages`
- [ ] `<html lang={locale}>` set dynamically
- [ ] `loading="eager"` / `preload` on LCP images
- [ ] `sizes` prop on responsive images
- [ ] Font loading with CLS prevention
- [ ] `lazyOnload` for non-critical third-party scripts
- [ ] `generateStaticParams` for known URL pages

### 10.10 Common Mistakes to Avoid

- Mixing `next-seo` package with the built-in Metadata API
- Missing canonical links (especially with dynamic routes)
- Using CSR for SEO-critical pages
- `robots.txt` blocking CSS/JS assets needed for rendering
- Missing OG images (reduces social sharing CTR)
- Duplicate title/description tags
- Sitemap with wrong domain or missing pages
- Not sanitizing JSON-LD output (XSS vulnerability)
- Using `next/script` instead of native `<script>` for JSON-LD

Sources: [Next.js Docs — Metadata & OG Images](https://nextjs.org/docs/app/getting-started/metadata-and-og-images), [Next.js Docs — generateMetadata](https://nextjs.org/docs/app/api-reference/functions/generate-metadata), [Next.js Docs — Sitemap](https://nextjs.org/docs/app/api-reference/file-conventions/metadata/sitemap), [Next.js Docs — Robots](https://nextjs.org/docs/app/api-reference/file-conventions/metadata/robots), [Next.js Docs — Internationalization](https://nextjs.org/docs/app/guides/internationalization), [Next.js Docs — JSON-LD](https://nextjs.org/docs/app/guides/json-ld), [Next.js Docs — Images](https://nextjs.org/docs/app/getting-started/images), [Next.js Docs — Fonts](https://nextjs.org/docs/app/getting-started/fonts), [Next.js Learn — SEO](https://nextjs.org/learn/seo/introduction-to-seo)

---

## 11. Key Metrics & Measurement

### 11.1 Traditional SEO Metrics

- **Organic traffic** (Google Search Console, GA4)
- **Keyword rankings** (Semrush, Ahrefs)
- **Click-through rate** from SERPs
- **Core Web Vitals** (PageSpeed Insights, CrUX data)
- **Indexation coverage** (Search Console)
- **Backlink profile** (Ahrefs, Semrush)
- **Domain Authority / Domain Rating** (Moz / Ahrefs)

### 11.2 AI-SEO Metrics (New)

- **AI citation frequency** — how often your brand/domain appears in AI answers
- **Citation position** — primary source vs. supporting mention
- **AI referral traffic** — traffic from ChatGPT, Perplexity, etc. (track via UTM or referrer)
- **Brand mention sentiment** in AI responses
- **Content type cited** — which content formats get picked up
- **Cross-platform visibility** — presence across Google AI, ChatGPT, Perplexity, Gemini

### 11.3 Recommended Tools

| Tool | Best For | Tier |
|------|----------|------|
| Google Search Console | Indexation, CTR, impressions, Core Web Vitals | Free |
| Google Analytics 4 | Traffic analysis, conversions | Free |
| PageSpeed Insights | Core Web Vitals testing | Free |
| Google Rich Results Test | Schema validation | Free |
| Semrush | Keyword research, competitor analysis, LLM visibility tracking | Paid |
| Ahrefs | Backlink analysis, content gap analysis | Paid |
| Screaming Frog | Technical SEO audits | Freemium |

### 11.4 Lily Ray's Measurement Advice

- Stop relying on synthetic AI rank tracking
- Focus on **first-party log files** for real user data
- Track brand searches as a proxy for AI-driven awareness
- Cut traffic projections in half for 2026 to account for AI search impact

---

## 12. Actionable Recommendations

### Immediate (Quick Wins)

1. **Audit and implement structured data** — JSON-LD for Organization, WebSite, JobPosting, BreadcrumbList, FAQ
2. **Fix Core Web Vitals** — target all three metrics in "Good" range
3. **Add/fix hreflang annotations** — ensure symmetric, self-referencing, with x-default
4. **Optimize meta titles and descriptions** — unique per page, keyword-inclusive, compelling
5. **Validate robots.txt and sitemap** — ensure all important pages are crawlable and indexed

### Short-Term (1-3 months)

6. **Structure content for AI extraction** — direct answers first, clear headings, lists, tables, FAQ sections
7. **Build topical authority** — hub-and-spoke content clusters around core topics
8. **Implement comprehensive Next.js Metadata API** — generateMetadata, OG images, canonical URLs
9. **Start monitoring AI citations** — query ChatGPT, Perplexity, Google AI Mode with key terms weekly
10. **Optimize for zero-click** — target featured snippets and PAA with 40-60 word direct answers

### Medium-Term (3-6 months)

11. **Develop GEO strategy** — create content specifically designed for AI citation
12. **Build cross-platform consensus** — consistent brand presence across owned site, YouTube, Reddit, review platforms
13. **Invest in original research/data** — unique data and insights are the most citable content type
14. **Author credibility program** — author bios, expert credentials, bylines on content
15. **Track and adapt** — establish AI-SEO metrics baseline and iterate based on data

### Ongoing

16. **Publish consistently** — the #1 ranking factor; maintain a regular cadence of high-quality content
17. **Keep content fresh** — update key pages regularly; AI systems favor recency
18. **Monitor algorithm updates** — adjust strategy after each core update
19. **Dual optimization** — every piece of content should be optimized for both traditional search and AI search
20. **Quality over quantity** — depth, originality, and real expertise over volume

---

## Appendix: Full Source List

### Official / Academic Sources
- [Google Search Essentials](https://developers.google.com/search/docs/essentials)
- [Google: Creating Helpful, Reliable, People-First Content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
- [Google Search Quality Rater Guidelines (Sept 2025)](http://www.google.com/insidesearch/howsearchworks/assets/searchqualityevaluatorguidelines.pdf)
- [Google: Intro to Structured Data](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data)
- [Google: Managing Multi-Regional Sites](https://developers.google.com/search/docs/specialty/international/managing-multi-regional-sites)
- [GEO: Generative Engine Optimization (arXiv)](https://arxiv.org/abs/2311.09735)
- [GEO at KDD 2024](https://dl.acm.org/doi/10.1145/3637528.3671900)
- [Next.js Docs — Metadata & OG Images](https://nextjs.org/docs/app/getting-started/metadata-and-og-images)
- [Next.js Docs — generateMetadata](https://nextjs.org/docs/app/api-reference/functions/generate-metadata)

### Industry Leaders (High Trust)
- [Search Engine Land — GEO Guide 2026](https://searchengineland.com/mastering-generative-engine-optimization-in-2026-full-guide-469142)
- [Search Engine Land — What is GEO](https://searchengineland.com/what-is-generative-engine-optimization-geo-444418)
- [Search Engine Land — 2025 Algorithm Updates Review](https://searchengineland.com/google-algorithm-updates-2025-in-review-3-core-updates-and-1-spam-update-466450)
- [Search Engine Land — SEO Priorities 2025](https://searchengineland.com/seo-priorities-2025-453418)
- [Search Engine Land — International SEO Guide](https://searchengineland.com/guide/international-seo)
- [Lily Ray — Reflection on SEO & AI Search 2025](https://lilyraynyc.substack.com/p/a-reflection-on-seo-and-ai-search)
- [Lily Ray — Tech SEO Connect 2025 Takeaways](https://lilyray.nyc/tech-seo-connect-2025-summary-takeaways/)
- [Backlinko — Google Ranking Factors (2026)](https://backlinko.com/google-ranking-factors)
- [Search Engine Journal — Google Algorithm History](https://www.searchenginejournal.com/google-algorithm-history/)

### Industry Research (Medium Trust)
- [First Page Sage — Google Algorithm Ranking Factors 2025](https://firstpagesage.com/seo-blog/the-google-algorithm-ranking-factors/)
- [First Page Sage — SEO Best Practices 2026](https://firstpagesage.com/seo-blog/seo-best-practices/)
- [First Page Sage — GEO Best Practices 2026](https://firstpagesage.com/seo-blog/generative-engine-optimization-best-practices/)
- [WordStream — SEO Ranking Factors 2025](https://www.wordstream.com/blog/seo-ranking-factors-2025)
- [WordStream — GEO vs SEO](https://www.wordstream.com/blog/generative-engine-optimization)
- [Conductor — Answer Engine Optimization](https://www.conductor.com/academy/answer-engine-optimization/)
- [LLMrefs — AEO Complete Guide](https://llmrefs.com/answer-engine-optimization)
- [Tryprofound — AEO Playbook 2025](https://www.tryprofound.com/resources/articles/answer-engine-optimization-aeo-guide-for-marketers-2025)
- [Tryprofound — AI Citation Patterns](https://www.tryprofound.com/blog/ai-platform-citation-patterns)
- [Yext — AI Visibility 2025](https://www.yext.com/blog/2025/10/ai-visibility-in-2025-how-gemini-chatgpt-perplexity-cite-brands)
- [Keywords Everywhere — E-E-A-T Guide](https://keywordseverywhere.com/blog/google-e-e-a-t-guidelines-an-overview/)
- [Sapt — AI Search Optimization Guide](https://sapt.ai/insights/ai-search-optimization-complete-guide-chatgpt-perplexity-citations)
- [Averi — ChatGPT vs Perplexity Citation Benchmarks 2026](https://www.averi.ai/how-to/chatgpt-vs.-perplexity-vs.-google-ai-mode-the-b2b-saas-citation-benchmarks-report-(2026))

### Technical / Next.js Sources (Official Docs — High Trust)
- [Next.js Docs — Metadata & OG Images](https://nextjs.org/docs/app/getting-started/metadata-and-og-images)
- [Next.js Docs — generateMetadata API](https://nextjs.org/docs/app/api-reference/functions/generate-metadata)
- [Next.js Docs — Sitemap](https://nextjs.org/docs/app/api-reference/file-conventions/metadata/sitemap)
- [Next.js Docs — Robots](https://nextjs.org/docs/app/api-reference/file-conventions/metadata/robots)
- [Next.js Docs — Internationalization](https://nextjs.org/docs/app/guides/internationalization)
- [Next.js Docs — JSON-LD Guide](https://nextjs.org/docs/app/guides/json-ld)
- [Next.js Docs — Image Optimization](https://nextjs.org/docs/app/getting-started/images)
- [Next.js Docs — Image Component API](https://nextjs.org/docs/app/api-reference/components/image)
- [Next.js Docs — Font Optimization](https://nextjs.org/docs/app/getting-started/fonts)
- [Next.js Docs — Script Component](https://nextjs.org/docs/app/api-reference/components/script)
- [Next.js Docs — Server & Client Components](https://nextjs.org/docs/app/getting-started/server-and-client-components)
- [Next.js Docs — Caching](https://nextjs.org/docs/app/getting-started/caching)
- [Next.js Docs — Route Groups](https://nextjs.org/docs/app/api-reference/file-conventions/route-groups)
- [Next.js Learn — SEO Course](https://nextjs.org/learn/seo/introduction-to-seo)

### Technical / Next.js Sources (Community — Medium Trust)
- [Adeel Imran — Complete Next.js SEO Guide](https://www.adeelhere.com/blog/2025-12-09-complete-nextjs-seo-guide-from-zero-to-hero)
- [AverageDevs — Next.js SEO Best Practices (App Router, 2025)](https://www.averagedevs.com/blog/nextjs-seo-best-practices)
- [SlateByte — Next.js SEO 2025](https://www.slatebytes.com/articles/next-js-seo-in-2025-best-practices-meta-tags-and-performance-optimization-for-high-google-rankings)
- [Strapi — Next.js SEO Guide](https://strapi.io/blog/nextjs-seo)

### Algorithm Update Analysis
- [GSQI — December 2025 Core Update Analysis](https://www.gsqi.com/marketing-blog/google-december-2025-broad-core-update-analysis-findings/)
- [Dataslayer — December 2025 Update: E-E-A-T & CWV Changes](https://www.dataslayer.ai/blog/google-core-update-december-2025-what-changed-and-how-to-fix-your-rankings)
- [Digisensy — 2025 Algorithm Updates Review](https://www.digisensy.com/google-algorithm-updates-of-2025-3-core-updates-1-spam-update-a-full-seo-review-for-2026/)

### Structured Data & Schema
- [Schema.org](https://schema.org/)
- [SEO Strategy — JSON-LD Guide](https://www.seostrategy.co.uk/schema-structured-data/json-ld-guide/)
- [digidop — Structured Data for SEO & GEO 2026](https://www.digidop.com/blog/structured-data-secret-weapon-seo)

### Additional Industry Sources
- [Sitebulb — SEO in 2026: 17 Expert Tips](https://sitebulb.com/resources/guides/seo-in-2026-17-expert-tips-predictions/)
- [Evergreen Media — SEO Trends 2026](https://www.evergreen.media/en/guide/seo-this-year/)
- [AuraMetrics — Technical SEO 2026 Guide](https://aurametrics.io/en/blog/technical-seo-2025-trends-2026-guide)
- [DOJO AI — What is GEO (2026 Guide)](https://www.dojoai.com/blog/what-is-geo-generative-engine-optimization-a-2026-guide)
