# Step: Final Reflection

Review your reflections from this run:

{reflections}

## Contribute to the knowledge base

For any reflection that describes a **novel, generalizable** problem (not company-specific),
add it to the troubleshooting knowledge base:

```bash
ws task learn --step <step_id> \
  --symptom "<what went wrong>" \
  --solution "<what fixed it>" \
  --tags "<comma-separated tags>"
```

**Skip** entries marked "none" and problems that are company-specific (e.g., "this company
uses a custom CMS" with no general lesson).

**Good KB entries** describe patterns that other agents will encounter:
- "Sitemap returns non-job URLs" → "Add url_filter to config"
- "Probe detects greenhouse but token is wrong" → "Extract token from page source"
- "API returns paginated results with small page size" → "Increase result_limit parameter"

Prefer entries that include:
- what was observed
- how it was observed
- why the solution likely generalizes

## When done

```bash
ws task complete
```
