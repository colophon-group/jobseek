# LLM Content Mirror

## Problem

The site's public pages use client-side React components (`"use client"`) for
interactivity and Lingui i18n macros.  This means the initial HTML returned by
the server contains only an empty shell — no readable text.  AI assistants
(Claude, GPT, Perplexity) and search-engine indexers that do not execute
JavaScript see a blank page plus whatever is in `<script>` / JSON-LD tags.

Previously, a hidden `<div>` in the root layout injected the same API
documentation on every page.  AI crawlers treated this as the page content,
causing them to describe every page as "an API endpoint" regardless of its
actual purpose.

## Solution

Each public page's server component (`page.tsx`) now renders a
`<LlmContentMirror>` block after the client component.  This block:

1. Is wrapped in `<div hidden aria-hidden="true">` — invisible to sighted
   users but present in the DOM for crawlers.
2. Includes a short preamble explaining that the block mirrors the
   JS-rendered content above.
3. Contains the **same text** the user sees, rendered server-side using the
   Lingui `i18n._()` API with the same message catalog.

The API documentation is no longer injected into page HTML.  It lives in
dedicated machine-readable files:

- `/.well-known/llms.txt` — prose description + API overview
- `/.well-known/ai-plugin.json` — ChatGPT plugin manifest
- `/api/openapi.json` — full OpenAPI spec

These are referenced from `robots.txt` and the homepage mirror.

## Why not just SSR the components?

The client components depend on browser APIs (locale negotiation from
cookies, theme detection, scroll-restoration, interactive accordions) and
Lingui's Babel macro, which produces optimal bundles only when compiled as
client code.  Converting them to RSC would require significant refactoring
and lose interactivity.

The mirror approach is a pragmatic compromise: pages that are important for
SEO / AI discoverability (homepage, about, FAQ, legal pages) get their
content duplicated in a server-rendered hidden block.  App pages behind
authentication (explore, company, my-jobs) do not need mirrors because they
are not indexed.

## Adding a mirror to a new page

1. Import `LlmContentMirror` from `@/components/LlmContentMirror`.
2. In the page's server component, after the client component, add:

```tsx
<LlmContentMirror>
  <h1>{i18n._({ id: "page.title", message: "..." })}</h1>
  <p>{i18n._({ id: "page.description", message: "..." })}</p>
  {/* ... */}
</LlmContentMirror>
```

3. Use the same `i18n._()` calls with the same message IDs as the client
   component so translations stay in sync.

## Files

- `src/components/LlmContentMirror.tsx` — the wrapper component
- `app/[lang]/(public)/*/page.tsx` — each page's mirror content
- `app/[lang]/layout.tsx` — root layout (API div removed)
