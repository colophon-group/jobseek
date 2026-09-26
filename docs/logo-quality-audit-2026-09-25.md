# Company icon source quality audit — 2026-09-25

## Why this was needed

The resolver instructions described `icon_url` as a "minified square icon".
That conflated compact display size with source image resolution. `ws` also
selected a PNG preview before the downloaded original when given a candidate
index, which could discard vector and high-resolution source quality.

The image upload workflow already converts raster icon sources to WebP with a
128-pixel maximum dimension. The resolver should select the best-quality
original of the chosen artwork; the uploader handles display optimization.

## Fleet scan

`scripts/audit_company_icons.py` fetched the current public assets for all
5,997 companies in `companies.csv`. It compared rasterized artwork on white
and black backgrounds at the same display size. Automatic staging requires:

- Both URLs on the project asset host, with the better logo inside the same
  company path.
- A raster icon under 72 pixels on its longest side, and a logo with at least
  twice that resolution (or a vector logo).
- Aspect ratios within 3% and mean absolute pixel difference no greater than
  8/255 on both backgrounds.
- No existing image staging directory for the company.

| Outcome | Companies |
|---|---:|
| Strict match, staged automatically | 23 |
| Similar artwork, visual review required | 34 |
| Icon already at least 96 pixels or SVG | 3,898 |
| Logo source below the 2× improvement threshold | 560 |
| Different artwork or substantial visual difference | 1,274 |
| URL absent or already shared | 94 |
| Fetch/decode error or asset outside project host | 114 |

The 34 borderline matches were reviewed side by side. Another 29 were staged
using [the dated review manifest](logo-quality-reviewed-2026-09-25.csv), which
pins both URLs and SHA-256 hashes. Five were left unchanged:

- `banking-talent`: the larger source is still only 32×32.
- `palmetto-clean-technology`: the images use different crops/layouts.
- `relay-graduate-school-of-education`: the larger file appears to be an
  enlarged pixelated image, so it offers no clear quality gain.
- `stealth-start-up-mobility-berlin`: the current 80×80 icon is already above
  the 72-pixel repair threshold.
- `wavestone`: the compact mark differs from the full logo.

In total, 52 icon sources were staged under `apps/crawler/data/images/`. The
existing PR image workflow will convert raster sources to WebP, upload the
replacement icons, update only their `icon_url` CSV fields, and remove the
staging files. The primary `logo_url` fields remain as they are.

The 114 errors are explicit in the generated report and were not changed by
this repair. They include malformed historical assets, missing R2 objects,
and URLs outside the project asset host. The audit is conservative: a
different mark may still be a correct icon, and a low-quality source without
a better matching logo needs separate brand research.

## Repeat the audit

From the repository root:

```bash
apps/crawler/.venv/bin/python scripts/audit_company_icons.py \
  --output /tmp/jobseek-icon-audit.csv
```

The default mode writes a report only. `--stage` stages strict matches.
`--reviewed-manifest <path>` may accompany `--stage` for visually reviewed
borderline matches; URL and content hashes must match the fresh audit.

## Prevention in `ws`

The active parallel logo track and legacy setup instructions now state that
"compact" describes UI use, not source dimensions. They direct the resolver
to use the same high-quality original for both fields when the artwork is
identical, and to retain a distinct compact mark when it really is different.
Candidate selection now takes the original artifact before the PNG preview.
