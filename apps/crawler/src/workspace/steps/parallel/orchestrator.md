# Parallel Pipeline — {{ slug }}

## Setup

{% if not company_name %}
Company details not yet configured. Start by setting name and website:

```bash
ws set --name "..." --website "..." --no-discover
```
{% endif %}

## Spawn parallel tracks

Launch these as **background subagents** simultaneously. Read each
prompt template and pass it as the subagent's task description.

**Prompt templates directory:** `{{ prompts_dir }}/`

- **Track A (enrichment):** Fill descriptions (4 locales), industry,
  employee count, founded year.
  Read: `{{ prompts_dir }}/track-a-enrichment.md`
- **Track B (logos):** Discover and select logo + icon.
  Read: `{{ prompts_dir }}/track-b-logos.md`
- **Track C (boards):** Find all career boards — add each with
  `ws add board`. Work progressively, not all-at-once.
  Read: `{{ prompts_dir }}/track-c-boards.md`

Replace template variables before passing to the subagent:
- `{{ "{{" }} slug {{ "}}" }}` → `{{ slug }}`
- `{{ "{{" }} website {{ "}}" }}` → `{{ website }}`
- `{{ "{{" }} company_name {{ "}}" }}` → `{{ company_name }}`
- `{{ "{{" }} issue {{ "}}" }}` → `{{ issue }}`

Tracks A and B are fire-and-forget — check results before submit.
Track C yields boards progressively — start processing each board
as it's added.

## Process boards

Use `ws await-board` to block until Track C adds a board, then process
it immediately. Repeat until no more boards arrive (timeout).

```
processed=""
while ws await-board --exclude $processed; do
    # await-board prints the new board alias
    # Process it: probe, test configs, feedback
done
```

For each new board:

1. `ws await-board --exclude <already-processed-aliases>`
   — blocks until a new board appears (2s polling, 120s timeout)
2. `ws probe monitor -n <expected-job-count> --board <alias>`
3. Identify top 2-3 monitor+scraper combinations from probe results
4. Spawn **parallel subagents** to test each combination.
   Read: `{{ prompts_dir }}/config-tester.md`
   Use `--config <name>` flag on `ws run` to avoid active_config races.
5. Collect results, compare.
   Read: `{{ prompts_dir }}/config-comparison.md`
6. Pick the best config: `ws select config <name> --board <alias>`
7. Record feedback: `ws feedback --board <alias> ...`
8. Add this alias to the exclude list and loop back to step 1.

When `ws await-board` times out (exit code 1), all boards are processed.

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
