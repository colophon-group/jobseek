# Reconfig Tester: {{ board_alias }}

Board: `{{ board_alias }}` (`{{ board_url }}`)
Workspace: `{{ slug }}`
Starting at: `{{ start_step }}`

## Previous configuration

{% if previous_monitor %}
- **Monitor:** {{ previous_monitor }}{% if previous_jobs %} (found {{ previous_jobs }} jobs){% endif %}
{% endif %}
{% if previous_scraper %}
- **Scraper:** {{ previous_scraper }}
{% endif %}

## Reconfig reason

{{ reconfig_reason }}

## Assignment

Investigate what broke and fix the configuration. Reuse what still works —
do not re-test components that haven't changed unless you have evidence
they're also broken.

{% if start_step == 'select_scraper' %}
### Scraper-only fix

The monitor is assumed to still work. Start by running the existing monitor
to get fresh sample URLs, then test scraper configurations:

```bash
ws run monitor --board {{ board_alias }}
ws probe scraper --board {{ board_alias }}
```

If the monitor also returns unexpected results (0 jobs, different count),
report that — the main agent may need to backtrack to monitor selection.

{% elif start_step == 'select_monitor' %}
### Monitor fix

Re-probe and test monitor configurations:

```bash
ws probe monitor -n <expected-job-count> --board {{ board_alias }}
```

If the previous monitor type still works, try config variations first
(`ws help monitor {{ previous_monitor }}`). Only switch types if the
previous one is fundamentally broken.

{% elif start_step == 'add_boards' %}
### Board URL changed

The board URL may have changed. Investigate the company careers page and
find the current board URL:

```bash
ws del board {{ board_alias }}
ws add board <alias> --url "<new-url>"
```

Then proceed with monitor probing on the new board.
{% endif %}

## Verification

After fixing, verify:
- Job count is reasonable (compare with what the website shows)
- Required fields extract cleanly: title, location, description
- Descriptions have substance (not one-liners or boilerplate)

Record feedback:

```bash
ws feedback --board {{ board_alias }} --title <quality> --description <quality> \
  --locations <quality> --verdict <level> --verdict-notes "<explanation>"
```

## Report

Report your result:
- **Status:** success or failure
- **What changed:** what was broken and how you fixed it
- **Config:** final monitor type + scraper type
- **Job count:** before vs after
- **Issues:** anything the main agent should know
