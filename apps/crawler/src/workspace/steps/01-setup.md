# Step: Claim Issue and Configure Company

## 1. Claim the issue

```bash
ws new {slug} --issue {issue}
```

## 2. Set company details and discover logos

Set the name and website — this triggers auto-discovery of logo/icon candidates:

```bash
ws set --name "<Company Name>" --website "<homepage URL>"
```

## 3. Verify and select candidates

**Visually verify each candidate artifact.** Read each file and confirm whether it's a
real logo or icon (not a banner, hero image, or unrelated graphic).

Then select by candidate number:

```bash
ws set --logo-candidate 1 --icon-candidate 2
```

If none of the candidates are good, find a better image on the company's website
(press page, about page, footer) and provide the URL directly:

```bash
ws set --logo-url "<direct image URL>" --icon-url "<direct icon URL>"
```

## 4. Verify final images

**You must visually verify the final files.** Read each PNG and confirm:

- **Logo**: Is this the company's actual logo or brand mark? Reject generic images,
  banners, photos, hero images, or unrelated graphics.
- **Icon**: Is this a recognizable favicon/icon for the company? It should be a small
  square image, typically the company's logomark or initials.

If either image is wrong, re-run `ws set` with a different `--logo-candidate`/`--icon-candidate`
or provide your own `--logo-url`/`--icon-url`.

## When done

```bash
ws task next --notes "none"
```
