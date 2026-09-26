# Track B: Logo Discovery and Selection

Workspace: `{{ slug }}`
Website: {{ website }}


## Goal

Find and select a brand-correct primary logo and an asset for compact UI for
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

The auto-ranking scores and candidate roles are hints, not reliable decisions.
Visually confirm the selected artwork and compare source dimensions and format.
The JPEG files are previews only; `ws` saves the original selected image for
upload. A favicon at 16×16 or 32×32 pixels is usually too small for our UI.

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

- **Logo** = primary logo artwork (the one used in headers or press kits).
- **Icon** = artwork that remains recognizable in compact UI. "Compact" refers
  to where we display it, **not** to the source image's pixel dimensions or
  file size. The upload pipeline creates an optimized 128-pixel WebP icon from
  a raster source; give it the best available source.
- If the icon and full logo show **the same artwork**, select the higher-quality
  original for **both** fields, even if discovery labels a low-resolution
  favicon as `icon`. The same candidate index or direct URL may be used twice.
  Prefer a vector SVG or a larger raster image over a 16/32/48-pixel favicon.
- If the compact mark is visually different from the full logo (for example,
  a symbol without the wordmark), use that distinct mark, but find its
  highest-quality official source. Do not replace it with the full logo merely
  because the full logo has more pixels.
- For raster sources, aim for at least 128 pixels on the relevant axis when
  available. Inspect both artwork and dimensions; file byte size alone is not
  a quality measure.
- Prefer transparent-background assets (PNG/SVG over JPEG)
- Use direct image file URLs, not HTML pages containing images
- If only one suitable artwork exists, use it for both logo and icon.
