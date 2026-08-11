# Homepage image fetch-priority verification

Issue: #6640

Measured: 2026-08-11

Tooling: Lighthouse 12.8.2 plus the in-app Chromium browser

## Decision

The Astrologer is the mobile LCP element, so the hero artwork is loaded eagerly
with `fetchPriority="high"`. The after-Pricing Miser is outside the initial
viewport at both measured sizes and is left lazy with no preload. This follows
Next.js 16's recommendation to prefer eager loading or high fetch priority over
the deprecated `priority` alias when an image is discoverable in the body.
React/Next emits one responsive preload for that eager high-priority image, and
the link itself carries `fetchpriority="high"`. The two Astrologer theme assets
have identical alpha and exactly inverted visible RGB pixels. The hero therefore
uses one invariant light-theme source and the `.dark` class inverts it in CSS
before first paint. It does not swap the LCP image URL during hydration or a
theme toggle.

## Before: deployed production

The browser DOM at 375 x 812 placed the Astrologer at y=627..1013 with
`loading="lazy"`. It placed the Miser at y=5934..6294 with a responsive image
preload and `loading="auto"`. At 1280 x 720 the corresponding positions were
y=84..526 and y=3789..4349; the same loading and preload attributes applied.

Lighthouse's simulated mobile run identified the Astrologer as LCP at 4,809 ms
and reported the lazy-loaded-LCP failure. In the observed request trace, the
Miser preload started at 548.7 ms and transferred 181,589 bytes before the
Astrologer started at 1,256.2 ms and transferred 174,196 bytes. Both requests
were for the image optimizer's 1080-pixel variant.

The desktop-preset run identified the first feature screenshot, rather than
either public-domain artwork, as LCP at 1,082 ms. This is why the priority
decision is based on the confirmed mobile candidate, not an assumption that
the hero artwork is LCP at every viewport.

## After: local production build

The built page at both viewports contains exactly one public-domain image
preload, for the Astrologer, with `fetchpriority="high"`. The Astrologer image
has `loading="eager"` and `fetchpriority="high"`; the Miser has
`loading="lazy"`, no fetch-priority attribute, and no preload. Default-dark and
persisted-light browser checks retain the same Astrologer URL; only the
computed CSS filter changes.

In the mobile Lighthouse trace, the Astrologer preload started at 46.4 ms with
High network priority and the Miser was not fetched during the measured load.
The Astrologer remained the LCP element and the lazy-loaded-LCP audit passed.
In the desktop trace, the Astrologer started at 47.2 ms as a High-priority
preload and the non-preloaded Miser started at 147.9 ms with Low priority; the
feature screenshot remained the desktop LCP element.

The production-before and local-after LCP durations are not presented as a
performance delta because the origins and image-optimizer cache state differ.
After deployment, repeat both Lighthouse runs against `https://jseek.co/en`
and verify the same preload/loading attributes and request ordering before
closing #6640.
