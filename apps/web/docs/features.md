# Features component

Implements the "bleed" showcase sections on the homepage (`Features.tsx`).

## Layout concept

Each section is a two-column row: a **text column** and a **screenshot image**.
The screenshot is intentionally wider than the viewport — it "bleeds" past
the screen edge and gets progressively clipped as the viewport narrows.

```
Section 1 (standard)          Section 2 (inverted)

┌─────────────────────┐       ┌─────────────────────┐
│ [text]    [img ──▶ ┃       ┃ ◀── img]    [text] │
└─────────────────────┘       └─────────────────────┘
  text left,                    text right,
  image bleeds right            image bleeds left
```

On mobile (< 1024px), sections stack vertically: text on top, image below.
The image still bleeds to the appropriate viewport edge.

```
Section 1 mobile         Section 2 mobile

┌──────────────┐         ┌──────────────┐
│  [text]      │         │      [text]  │
│  [img ────▶ ┃         ┃ ◀──── img]  │
└──────────────┘         └──────────────┘
  bleeds right             bleeds left
```

## Alignment

Text columns are inset to align with the global 1200px page container
(`max-w-[1200px] px-4`). This is computed via `ALIGN_PAD`:

```css
max(16px, calc((100vw - 1200px) / 2 + 16px))
```

The image side has **zero padding**, so it sits flush against the viewport edge.

## Image clipping mechanics

`ImageWrapper` sets `overflow: hidden` with `max-width: <screenshot-width>px`.
The inner `<img>` has a fixed pixel width (e.g. 1200px) with `max-width: none`,
so it overflows. The `justify-content` property controls which end clips:

| Variant    | `justify-content` | Clips from | Border-radius        |
|------------|-------------------|------------|----------------------|
| Standard   | `flex-start`      | Right      | `24px 0 0 24px`      |
| Inverted   | `flex-end`        | Left       | `0 24px 24px 0`      |

## Responsive breakpoints

| Viewport     | Behaviour                                                  |
|--------------|------------------------------------------------------------|
| < 1024px     | Stacked vertically. Text gets `px-4` on both sides.        |
| >= 1024px    | Side by side. Text max 520px, image fills remaining space. |
| >= 2448px    | Both edges pull inward; image gets full `24px` radius.     |

The 2448px breakpoint (`EXTRA_WIDE_BREAKPOINT`) is calculated as
`CONTAINER_MAX + 2 * screenshot-width + 2 * CONTAINER_PAD`, ensuring the
image is fully contained within the layout at ultra-wide resolutions.

## Theme handling

`ThemedImage` renders both a light and dark `<img>` element. The `ImageWrapper`
`<style>` block includes scoped display-toggle rules:

```css
.feat-img-std .themed-img-dark { display: none; }
.dark .feat-img-std .themed-img-light { display: none; }
.feat-img-std .themed-img-light,
.dark .feat-img-std .themed-img-dark { display: block; }
```

These **must** be more specific than the global toggle in `globals.css`
(`.themed-img-dark { display: none }`), because the `img` sizing rules in
the same block would otherwise force `display: block` on the wrong image.

## Constants

| Name                    | Value                          | Purpose                                  |
|-------------------------|--------------------------------|------------------------------------------|
| `CONTAINER_MAX`         | `1200`                         | Must match `max-w-[1200px]` on the page  |
| `CONTAINER_PAD`         | `16`                           | Must match `px-4` on the page container  |
| `TEXT_MAX_W`            | `520`                          | Flex-basis for the text column           |
| `IMAGE_BORDER_RADIUS`   | `24`                          | Rounded corner on the visible edge       |
| `EXTRA_WIDE_BREAKPOINT` | `2448`                        | Viewport where both edges pull inward    |
| `MEDIA_SHADOW`          | `0px 12px 32px rgba(…, 0.18)` | Drop shadow on the image wrapper         |
| `ALIGN_PAD`             | CSS `max()` expression         | Aligns text edge with the page container |

## Sub-components

- **`PointBlock`** — Icon + title + description row for a single feature bullet.
  Icons are mapped from string keys (from `siteConfig`) to Lucide components
  via `iconMap`.
- **`ImageWrapper`** — Overflow container with inline `<style>` for border-radius,
  image sizing, and theme toggles. Accepts `inverted` to flip alignment/rounding.
- **`FeatureSection1` / `FeatureSection2`** — Concrete section instances pulling
  config from `siteConfig.features.sections[0|1]`. Each renders its own row
  padding `<style>` block and wires up the text content with `<Trans>` i18n macros.

## Data source

Non-translatable config (screenshot paths, dimensions, icon keys) lives in
`src/content/config.ts` under `siteConfig.features.sections[]`.
Translatable strings (eyebrow, heading, description, point titles/descriptions)
are inline via Lingui `<Trans>` macros with dot-namespaced IDs.
