# Step: Claim Issue and Configure Company

## 1. Claim the issue

```bash
ws new {slug} --issue {issue}
```

## 2. Set company details and discover brand assets

Set the name and website — this triggers auto-discovery of full/minified logo candidates:

```bash
ws set --name "<Company Name>" --website "<homepage URL>"
```

## 3. Verify and select candidates

**Visually verify each candidate artifact.** Read each file and confirm whether it's a
real brand asset (not a banner, hero image, or unrelated graphic).
Candidate artifacts are saved as both the original file and a PNG preview in
`artifacts/company/logo-candidates/`.

Then select by candidate number:

```bash
ws set --logo-candidate 1 --icon-candidate 2
```

If none of the candidates are good, find a better image on the company's website
(press page, about page, footer) and provide the URL directly:

```bash
ws set --logo-url "<direct full-logo URL>" --icon-url "<direct square-logo URL>"
```

Use direct image file URLs (not HTML pages). Transparent background is preferred
for both assets when available.

## 4. Verify final images

**You must visually verify the final files.** Read each PNG and confirm:

- **Logo (`logo_url`)**: Is this the company's preferred full primary logo
  (wordmark/lockup when available)? Reject generic images, banners, photos,
  hero images, or unrelated graphics.
- **Icon (`icon_url`)**: Is this a recognizable minified square logo/icon for
  compact UI (typically a logomark or initials)?
- **Background**: Prefer transparent-background assets for both `logo_url` and
  `icon_url` when available from official sources (fallbacks are acceptable).

If either image is wrong, re-run `ws set` with a different `--logo-candidate`/`--icon-candidate`
or provide your own `--logo-url`/`--icon-url`.

## When done

```bash
ws task next --notes "none"
```
