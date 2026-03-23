# Track B: Logo Discovery and Selection

Workspace: `{{ slug }}`
Website: {{ website }}


## Goal

Find and select a brand-correct full logo and a minified square icon for
the company. This runs in parallel with metadata enrichment and board
configuration.

## Step 1: Trigger discovery

Background discovery may have already found logo candidates. Check
with `ws logos {{ slug }}` (this is the only logo-related command —
there is no `ws logo-candidates` or similar). If candidates exist,
inspect and select from them. If discovery is still running, wait a
moment and try again.

```bash
ws set {{ slug }} --website "{{ website }}"
```

This fetches the homepage and discovers logo candidates. Results are saved
as PNG previews in the artifacts directory. Review the output table — it
shows candidate index, role (logo/icon), score, source, and file paths.

## Step 2: Inspect and select

Look at the **JPEG previews** (`candidate-*.jpg`) in the artifacts to verify
brand correctness. **Do NOT read the PNG files** — some PNG variants cause
API errors. Always use the `.jpg` thumbnails for visual inspection.

The auto-ranking scores are hints, not reliable decisions — you must
visually confirm the selected assets are correct.

Select by candidate index:

```bash
ws set {{ slug }} --logo-candidate <N> --icon-candidate <N> --logo-type <type>
```

Logo type options:
- `wordmark` — text-only logo (e.g., "Google" in its typeface)
- `wordmark+icon` — text + symbol (e.g., "GitHub" + octocat)
- `icon` — symbol/mark only (e.g., Apple's apple)

## Fallback: manual URLs

If auto-discovery misses the logo, find it on the company's press/brand
page and set URLs directly:

```bash
ws set {{ slug }} --logo-url "<direct-image-url>" --icon-url "<direct-image-url>" --logo-type <type>
```

## Guidelines

- **Logo** = full primary logo (the one used in headers, press kits)
- **Icon** = minified square version (favicon, app icon, social media avatar)
- Prefer transparent-background assets (PNG/SVG over JPEG)
- Use direct image file URLs, not HTML pages containing images
- If only one format exists (e.g., company only has a wordmark),
  use it for both logo and icon
