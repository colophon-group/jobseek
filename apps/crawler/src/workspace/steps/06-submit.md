# Step: Submit

## Pre-submit checklist

Before submitting, verify:

- [ ] All boards discovered earlier are configured (or documented as subsets in `--verdict-notes`)
- [ ] Extracted content was manually verified — not just stats
- [ ] Each board has passing feedback recorded (`good` or `acceptable`)

## Submit

```bash
ws submit --summary "<difficulties, roadblocks, or unexpected behaviors>"
```

The `--summary` should focus on **difficulties encountered**, not just restate the result:

- Straightforward: `"Straightforward greenhouse config, 138 jobs"`
- Unexpected content: `"Sitemap had 200 URLs but only 40 were job pages; used path filter"`
- Multiple iterations: `"Tried sitemap (0 jobs), then dom monitor worked. JSON-LD missing locations, switched to dom scraper"`

Use `--force` to submit despite a `poor` verdict (not for `unusable`).

Write the summary as evidence + interpretation (not just command history).

## If submit fails

Run `ws resume` to diagnose and retry.

## When done

`ws submit` does not auto-advance the task workflow. Advance it explicitly:

```bash
ws task next --notes "<any issues during submit, or 'none'>"
```
