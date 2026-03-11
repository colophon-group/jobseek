# Step: Claim Issue and Configure Company

Use an evidence-first style: for each logo/icon decision, note what you saw,
where you saw it, and why it is likely the correct brand asset.

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
Do not assume `ws` can evaluate this for you: it can discover/download candidates,
but only manual visual inspection can confirm brand correctness.
Candidate artifacts are saved as both the original file and a PNG preview in
`artifacts/company/logo-candidates/`.

After visual checks, select by candidate number:

```bash
ws set --logo-candidate 1 --icon-candidate 2 --logo-type wordmark
```

If candidate evidence is weak, find a better image on the company's website
(press page, about page, footer) and provide the URL directly:

```bash
ws set --logo-url "<direct full-logo URL>" --icon-url "<direct square-logo URL>" --logo-type wordmark
```

Use direct image file URLs (not HTML pages). Transparent background is preferred
for both assets when available.
Set `--logo-type` to match the full logo variant: `wordmark`, `wordmark+icon`, or `icon`.

## 4. Verify final images

**You must visually verify the final files.** Read each PNG and confirm:

- **Logo (`logo_url`)**: Is this the company's preferred full primary logo
  (wordmark/lockup when available)? Reject generic images, banners, photos,
  hero images, or unrelated graphics.
- **Logo Type (`logo_type`)**: Label the full logo as `wordmark`, `wordmark+icon`, or `icon`.
- **Icon (`icon_url`)**: Is this a recognizable minified square logo/icon for
  compact UI (typically a logomark or initials)?
- **Background**: Prefer transparent-background assets for both `logo_url` and
  `icon_url` when available from official sources (fallbacks are acceptable).

If either image is wrong, re-run `ws set` with a different `--logo-candidate`/`--icon-candidate`
or provide your own `--logo-url`/`--icon-url`.

## 5. Verify company enrichment

Setting the website auto-fetches company metadata (description, industry, employee count,
founded year) from JSON-LD and Wikidata. Check the enrichment output.

If **description** or **industry** are missing (required), fill them manually:

```bash
ws set --description "One-sentence company description"
ws set --industry <id>
```

Use `ws help industries` to see available industry IDs. Optional fields:

```bash
ws set --employee-count-range <1-8> --founded-year <YYYY>
```

## When done

```bash
ws task next --notes "none"
```
