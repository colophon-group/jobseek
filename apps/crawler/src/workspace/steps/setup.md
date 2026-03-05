# Step: Claim Issue and Configure Company

## 1. Claim the issue

`ws new` creates the workspace, branch, stub CSV row, and draft PR.
It also sets the active workspace — all subsequent commands auto-resolve the slug.

```bash
ws new {slug} --issue {issue}
```

## 2. Set company details and discover logos

**First**, set the name and website — this triggers auto-discovery of logo/icon candidates:

```bash
ws set --name "<Company Name>" --website "<homepage URL>"
```

When `--website` is provided without `--logo-url` or `--icon-url`, `ws set` automatically
fetches the homepage, discovers logo/icon candidates using heuristics (JSON-LD, OG image,
apple-touch-icon, header/nav images, inline SVGs, favicon), downloads them, and prints a
ranked table of candidates.

Candidates are saved to:

    .workspace/{slug}/artifacts/company/logo-candidates/
      candidate-1.png   # e.g. JSON-LD Organization logo
      candidate-2.png   # e.g. apple-touch-icon
      candidate-3.svg   # e.g. inline SVG from header
      ...

## 3. Verify and select candidates

**Visually verify each candidate artifact.** Read each file and confirm whether it's a
real logo or icon (not a banner, hero image, or unrelated graphic).

Then select by candidate number:

```bash
ws set --logo-candidate 1 --icon-candidate 2
```

This resolves the candidate's URL from the saved metadata and downloads + converts the
final `logo.png` / `icon.png` artifacts.

**Alternative — provide URLs directly** (skips auto-discovery):

```bash
ws set --logo-url "<direct image URL>" \
  --icon-url "https://www.google.com/s2/favicons?domain=<domain>&sz=128"
```

URLs must be reachable — submit blocks on unreachable logo/icon URLs. Prefer logos hosted
on the company's own website (OG image, press/brand page, or page source).

## 4. Verify final images

**You must visually verify the final files.** Read each PNG file and confirm:

- **Logo**: Is this the company's actual logo or brand mark? Reject generic images,
  banners, photos, hero images, or unrelated graphics. A logo should clearly identify
  the company (wordmark, logomark, or combination).
- **Icon**: Is this a recognizable favicon/icon for the company? It should be a small
  square image, typically the company's logomark or initials.

If either image is wrong, re-run `ws set` with a different `--logo-candidate`/`--icon-candidate`
or provide your own `--logo-url`/`--icon-url`.

```bash
# Read the downloaded artifacts to verify visually
.workspace/{slug}/artifacts/company/logo.png
.workspace/{slug}/artifacts/company/icon.png
```

## When done

The gate auto-checks: name, website, and branch must all be set.
If the gate passes, you'll advance automatically.

```bash
ws task next --notes "none"
```
