# Config Comparison: {{ board_alias }}

Board: `{{ board_alias }}` (`{{ board_url }}`)
Workspace: `{{ slug }}`

## Subagent Results

{{ results }}

## Selection Criteria

Pick the best config using this priority order:

1. **Required fields all clean** — title, location, description must all be
   `clean`. Configs with any required field `noisy` or worse are eliminated.
2. **Most optional fields** — among configs passing requirement 1, prefer
   the one extracting the most optional fields cleanly (employment_type,
   date_posted, salary, etc.).
3. **Lowest cost** — among configs tied on field coverage, prefer the
   cheapest (lowest monitor_per_cycle + scraper_per_job).
4. **Rich preferred** — a rich monitor (no scraper needed) is preferred
   over a URL-only monitor + scraper at similar quality, because it's
   simpler and more resilient.

## Action

Set the winning config as active:

```bash
ws select config <best-config-name> --board {{ board_alias }}
```

If feedback was already recorded by the subagent, you're done with this
board. Otherwise record feedback now.

## If all configs failed

- Review the failure reports for common patterns
- Try a manual approach: pick the closest-to-working config and iterate
  its configuration (`ws help monitor <type>`, `ws help scraper <type>`)
- If no config can extract required fields: `ws task fail --reason "..."`
- Consider `ws task back --to add_boards` if the board URL itself is wrong
