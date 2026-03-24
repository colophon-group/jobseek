# Parallel Pipeline — {{ slug }}

## Setup

{% if not company_name %}
Company details not yet configured. Start by setting name and website:

```bash
ws set {{ slug }} --name "..." --website "..." --no-discover
```
{% endif %}

## Spawn parallel tracks

Launch these as **background subagents** simultaneously. Pass each
rendered prompt below as the subagent's task description — no file
reads or variable substitution needed.

Tracks A and B are fire-and-forget — check results before submit.
Track C yields boards progressively — start processing each board
as it's added.

> **Scope is global, not locale-specific.** The user's country in the
> GitHub issue is where the request came from, **not a geographic filter**.
> Configure ALL of the company's career boards worldwide — do not restrict
> to a single country or region. Never add query parameters like
> `?location=switzerland` or `?country=us` to board URLs. Use the
> unfiltered base URL so the crawler captures all listings.

### Track A — Enrichment

<track-a>
{{ track_a_prompt }}
</track-a>

### Track B — Logos

<track-b>
{{ track_b_prompt }}
</track-b>

### Track C — Board Discovery

<track-c>
{{ track_c_prompt }}
</track-c>

## Process boards

Use `ws await-board {{ slug }}` to block until Track C adds a board, then process
it immediately. Repeat until no more boards arrive (timeout).

`await-board` automatically tracks which boards it has already returned —
no need to pass `--exclude` flags.

```
while ws await-board {{ slug }}; do
    # await-board prints the new board alias
    # Process it: probe, test configs, feedback
done
```

For each new board:

1. `ws await-board {{ slug }}` — blocks until a new board appears (auto-tracks seen boards)
2. **Skip probing if ATS is already confirmed:** If a previous board from this
   company already identified a specific ATS (e.g., Greenhouse), and the new
   board URL is on the same ATS domain, skip probing — directly select the same
   monitor type with the board-specific token.
   Otherwise: `ws probe monitor {{ slug }} -n <expected-job-count> --board <alias>`
3. **Decide testing strategy based on probe results:**

   **Fast path (single test, no subagents):** If the probe's top result is a
   **known stable ATS** — greenhouse, ashby, lever, gem, recruitee, personio,
   workday, hireology, pinpoint, dvinci, traffit, rss — AND it matched with
   high confidence (detected via `can_handle`), test it directly yourself.
   No need to spawn subagents for an obvious choice. For companies with
   multiple boards on the same ATS, configure subsequent boards directly
   without re-probing.

   **Parallel path (2-3 subagents):** If the probe returns multiple plausible
   options with similar scores, OR the top result is a generic type (sitemap,
   dom, api_sniffer, nextdata), spawn parallel subagents to test each.
   Use the config-tester template below — fill in the board-specific variables.
   Use `--config <name>` flag on `ws run` to avoid active_config races.

4. If parallel: collect results, compare using the criteria below.
5. Pick the best config: `ws select config {{ slug }} <name> --board <alias>`
6. **Before recording feedback**, check if "acceptable" can become "good":
   - Absent fields available in JSON-LD? Switch scraper to `json-ld`.
   - Noisy field values? Use the `map` spec to normalize (`ws help fields`).
   - Monitor has titles but missing locations? Try `enrich` option.
   - Scraper coverage < 100%? Tune `timeout`/`wait` before accepting.
7. Record feedback: `ws feedback {{ slug }} --board <alias> ...`
8. Loop back to step 1.

When `ws await-board {{ slug }}` exits with code 1 (timeout or discovery complete),
all boards are processed.

### Config tester template

Fill in the `{variables}` and pass to a subagent.
**Remind every subagent: do NOT read source code files (src/core/, src/shared/, any .py).
Use only `ws` commands and `ws help`.**

<config-tester-template>
{{ config_tester_raw }}
</config-tester-template>

### Config comparison criteria

{{ config_comparison_raw }}

## Converge and submit

Before submitting, verify:
- All metadata fields set (descriptions x4, industry, logos)
- All boards configured and feedback recorded
- Job counts verified against website
- **Multinational check:** If the company has 500+ employees or offices in
  multiple countries but only 1 board was configured, discovery is likely
  incomplete. Investigate the careers page for regional or ATS-specific
  boards before submitting.

```bash
ws submit {{ slug }} [--summary "..."]
```

### Advance through final steps

After submit succeeds, advance to the reflect step:

```bash
ws task next --notes "<difficulties, key decisions, or 'none'>"
```

During reflection, contribute to the knowledge base:

- **Non-obvious problem solved?** Record it so future agents can find it:
  `ws task learn --step <step> --symptom "..." --solution "..." --tags "..."`
- **Complex board configuration?** Record a case study:
  `ws task casestudy --company {{ slug }} --monitor <type> --scraper <type> --tags "..." --summary "..."`
- Nothing noteworthy? Skip this — only record genuinely reusable lessons.

Then complete the workflow:

```bash
ws task complete
```

**Do NOT call `ws task complete` directly after `ws submit`.** The sequence
is: `ws submit` → `ws task next` (enters reflect) → `ws task complete`.

## If something goes wrong

- Subagent failed → investigate and re-run, or handle manually
- No boards found → investigate the website directly
- All configs failed for a board → try manually or `ws task fail --reason "..."`
- New evidence invalidates earlier decisions → `ws task back --to <step> --reason "..."`
- Edge cases → `ws task troubleshoot "<query>"`
