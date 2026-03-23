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

`await-board` automatically tracks which boards it has already returned —
no need to pass `--exclude` flags.

```
while ws await-board; do
    # await-board prints the new board alias
    # Process it: probe, test configs, feedback
done
```

For each new board:

1. `ws await-board` — blocks until a new board appears (auto-tracks seen boards)
2. `ws probe monitor -n <expected-job-count> --board <alias>`
3. **Decide testing strategy based on probe results:**

   **Fast path (single test, no subagents):** If the probe's top result is a
   **known stable ATS** — greenhouse, ashby, lever, gem, recruitee, personio,
   workday, hireology, pinpoint, dvinci, traffit, rss — AND it matched with
   high confidence (detected via `can_handle`), test it directly yourself.
   No need to spawn subagents for an obvious choice.

   **Parallel path (2-3 subagents):** If the probe returns multiple plausible
   options with similar scores, OR the top result is a generic type (sitemap,
   dom, api_sniffer, nextdata), spawn parallel subagents to test each.
   Read: `{{ prompts_dir }}/config-tester.md`
   Use `--config <name>` flag on `ws run` to avoid active_config races.

4. If parallel: collect results, compare.
   Read: `{{ prompts_dir }}/config-comparison.md`
5. Pick the best config: `ws select config <name> --board <alias>`
6. Record feedback: `ws feedback --board <alias> ...`
7. Loop back to step 1.

When `ws await-board` exits with code 1 (timeout or discovery complete),
all boards are processed.

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
