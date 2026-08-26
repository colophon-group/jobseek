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

This is the publication point: after validation and commit, `ws submit`
pushes the complete branch and creates (or recovers) its draft PR. `ws new`
does not publish a stub branch or PR.

The `--summary` should focus on **difficulties encountered**, not just restate the result:

- Straightforward: `"Straightforward greenhouse config, 138 jobs"`
- Unexpected content: `"Sitemap had 200 URLs but only 40 were job pages; used path filter"`
- Multiple iterations: `"Tried sitemap (0 jobs), then dom monitor worked. JSON-LD missing locations, switched to dom scraper"`

Use `--force` to submit despite a `poor` verdict (not for `unusable`).

Write the summary as evidence + interpretation (not just command history).

## If submit fails

Run `ws resume` to diagnose and retry.

## When done

`ws submit` posts stats and transcript but does **not** mark the PR ready for review.
`ws task complete` also leaves it draft. Independent exact-head review and
Required CI/CodeQL must complete before a human marks it ready.

Advance to the next step explicitly:

```bash
ws task next --notes "<any issues during submit, or 'none'>"
```
