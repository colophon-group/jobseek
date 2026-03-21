# Parallel Pipeline — {{ slug }}

## Setup

{% if not company_name %}
Company details not yet configured. Start by setting name and website:

```bash
ws set --name "..." --website "..." --no-discover
```
{% endif %}

## Spawn parallel tracks

Launch these as **background subagents** simultaneously:

- **Track A (enrichment):** Fill descriptions (4 locales), industry,
  employee count, founded year.
  Prompt template: `src/workspace/steps/parallel/track-a-enrichment.md`
- **Track B (logos):** Discover and select logo + icon.
  Prompt template: `src/workspace/steps/parallel/track-b-logos.md`
- **Track C (boards):** Find all career boards — add each with
  `ws add board`. Work progressively, not all-at-once.
  Prompt template: `src/workspace/steps/parallel/track-c-boards.md`

Read each template and render it with context (slug={{ slug }},
website={{ website }}, etc.) before passing to the subagent.
Use `from src.workspace.workflow import render_parallel_prompt` to render.

Tracks A and B are fire-and-forget — check results before submit.
Track C yields boards progressively — start processing each board
as it's added.

## Process boards

As Track C adds boards, process each one:

1. `ws probe monitor -n <expected-job-count> --board <alias>`
2. Identify top 2-3 monitor+scraper combinations from probe results
3. Spawn **parallel subagents** to test each combination using the
   config-tester template: `src/workspace/steps/parallel/config-tester.md`
   Use `--config <name>` flag on `ws run` to avoid active_config races.
4. Collect results, compare using config-comparison template:
   `src/workspace/steps/parallel/config-comparison.md`
5. Pick the best config: `ws select config <name> --board <alias>`
6. Record feedback: `ws feedback --board <alias> ...`

Run `ws help monitors` and `ws help scrapers` for reference.

## Converge and submit

Before submitting, verify:
- All metadata fields set (descriptions x4, industry, logos)
- All boards configured and feedback recorded
- Job counts verified against website

```bash
ws submit [--summary "..."]
ws task complete
```

## If something goes wrong

- Subagent failed → investigate and re-run, or handle manually
- No boards found → investigate the website directly
- All configs failed for a board → try manually or `ws task fail --reason "..."`
- New evidence invalidates earlier decisions → `ws task back --to <step> --reason "..."`
- Edge cases → `ws task troubleshoot "<query>"`
