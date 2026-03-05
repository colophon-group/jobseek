# Step: Add All Discovered Boards

Add **every** career page discovered during validation as a separate board, then probe each one.

## Add boards

For each board URL discovered in the validate step:

```bash
ws add board <alias> --url "<board-url>"
```

The alias is auto-prefixed with the company slug (e.g., `careers` → `{slug}-careers`).
The first board added is auto-activated.

**Alias naming conventions:**
- Single board: `careers`
- Regional boards: `careers-us`, `careers-de`, `careers-eu`
- Per-ATS boards: `careers-gh` (Greenhouse), `careers-lever`
- Departmental: `careers-engineering`, `careers-sales`

## Probe monitors — or skip if ATS is obvious

If the board URL matches a **known ATS domain**, skip probing and select the monitor directly
in the next step — probing is unnecessary:

| URL pattern | Monitor type |
|---|---|
| `boards.greenhouse.io/<token>` or `job-boards.greenhouse.io/<token>` | `greenhouse` |
| `jobs.lever.co/<token>` | `lever` |
| `jobs.ashbyhq.com/<token>` | `ashby` |
| `<company>.recruitee.com` | `recruitee` |
| `apply.workable.com/<token>` | `workable` |
| `<company>.jobs.personio.com` or `<company>.jobs.personio.de` | `personio` |
| `<company>.pinpointhq.com` | `pinpoint` |
| `<company>.mysmartrecruiters.com` or `careers.smartrecruiters.com/<token>` | `smartrecruiters` |
| `<company>.wd1.myworkdayjobs.com` (or wd2–wd5) | `workday` |
| `<company>.rippling.com/careers` | `rippling` |
| `<company>.hireology.com` | `hireology` |

For these, just add the board and move on — you will select the monitor in the next step.

**Otherwise**, probe to discover what works:

```bash
ws probe monitor -n <job-count>
```

If probes return 0 jobs for all types, run the deep probe:

```bash
ws probe deep -n <job-count>
```

Check the deep probe output for API discoveries and CMS detection results before trying manual approaches.

## Multiple boards

If you found multiple career pages (regional, departmental, or separate ATS instances):

```bash
ws add board careers-us --url "https://company.com/us/careers"
ws add board careers-de --url "https://company.com/de/careers"
```

**Do not skip boards that were discovered during validation.**
The issue URL is a starting point, not a scope constraint.

If one board's listings are a strict superset of another's (verified by comparing job counts
and sampling titles), the subset board can be skipped — document this in feedback `--verdict-notes` later.

## When done

The gate auto-checks: at least one board must be added and probed.

```bash
ws task next --notes "<how many boards, any surprises from probing>"
```
