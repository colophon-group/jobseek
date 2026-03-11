# Job Posting Enrichment

LLM-based structured data extraction from job posting descriptions. Runs as a batch process using the LLM provider's batch API for cost efficiency.

## Overview

The enricher reads job description HTML from R2, sends it through an LLM batch API, and writes structured fields back to the `job_posting.enrichment` JSONB column. It extracts information that scrapers cannot reliably determine from raw HTML — seniority, education requirements, visa policy, and benefit categorization.

## Extracted Fields

All fields are nullable. The LLM returns `null` when information is absent or ambiguous.

### `seniority`

Career level inferred from job title + requirements.

| Value | Meaning |
|---|---|
| `intern` | Internship, working student (Werkstudent, Praktikum, stage) |
| `entry` | Junior, graduate program, trainee, 0-2 years experience |
| `mid` | Mid-level, 2-5 years, no seniority qualifier in title |
| `senior` | Senior, Sr., 5+ years |
| `lead` | Team lead, tech lead, engineering lead |
| `staff` | Staff engineer/designer |
| `principal` | Principal engineer/architect |
| `director` | Director-level, VP (non-C-suite) |
| `executive` | C-suite, Managing Director, CEO, CTO, CFO |

### `education`

Minimum stated education requirement. "Or equivalent experience" still counts as that level.

| Value | Meaning |
|---|---|
| `none` | Explicitly states no degree required |
| `vocational` | Apprenticeship, trade school, Ausbildung, CFC/EFZ, BTS, apprendistato |
| `associate` | Associate degree, community college, DUT, DEUG |
| `bachelor` | Bachelor's, licence, Fachhochschule degree, laurea triennale |
| `master` | Master's, Diplom, DEA, laurea magistrale |
| `doctorate` | PhD, Dr., Promotion, doctorat |

### `experience`

Years of experience requirement. Object with `min` and `max` (integers, either can be null).

| Input | Output |
|---|---|
| "3+ years" | `{"min": 3, "max": null}` |
| "1-2 years" | `{"min": 1, "max": 2}` |
| "5 years" | `{"min": 5, "max": 5}` |
| Not mentioned | `null` |

### `visa_sponsorship`

Whether the employer offers visa/work permit sponsorship.

| Value | Meaning |
|---|---|
| `yes` | Explicitly offered ("we sponsor visas", "visa support available") |
| `no` | Requires existing authorization ("must have work permit", "valid work authorization required") |
| `null` | Not mentioned |

### `technologies`

List of specific named tools, frameworks, and programming languages. Proper casing, no generic categories.

- "PostgreSQL" not "postgres" or "databases"
- "React" not "react" or "frontend framework"
- "Kubernetes" not "k8s" or "container orchestration"

### `keywords`

5-10 lowercase search terms a job seeker would use. Domain-specific: role function, industry terms, specialization. Excludes technology names (covered by `technologies`) and generic words ("job", "company", "team").

Example: `["backend engineer", "fintech", "payments", "api design", "distributed systems"]`

### `benefits`

Standardized benefit identifiers. The LLM maps free-text benefits to these enum values.

| Value | Covers |
|---|---|
| `equity` | Stock options, RSUs, ESOP, VSOP |
| `bonus` | Performance/annual bonus, 13th/14th salary, Gratifikation |
| `retirement` | Company pension, 401(k), Betriebsrente, prévoyance, LPP/BVG, Pensionskasse |
| `signing_bonus` | One-time sign-on/welcome bonus |
| `relocation` | Relocation package, moving assistance |
| `health_insurance` | Supplementary/private health, Zusatzversicherung |
| `dental` | Dental plan, Zahnzusatzversicherung |
| `vision` | Vision plan, eye care coverage |
| `life_insurance` | Life/AD&D insurance |
| `disability_insurance` | Short/long-term disability, Invalidenversicherung |
| `mental_health` | Therapy, EAP, psychological support |
| `gym` | Fitness/sports membership, Urban Sports Club |
| `pto` | Paid time off, unlimited PTO (US-specific) |
| `parental_leave` | Parental leave beyond statutory minimum |
| `childcare` | Childcare subsidy, Kita, crèche, on-site nursery |
| `vacation_extra` | Vacation days above local statutory minimum |
| `sabbatical` | Sabbatical, extended leave option |
| `flexible_hours` | Flextime, Gleitzeit, core hours |
| `remote_budget` | Home office stipend, equipment budget |
| `education_budget` | Learning budget, conference attendance, Weiterbildungsbudget |
| `meal_allowance` | Meal vouchers, lunch subsidy, tickets restaurant |
| `public_transport` | Transit pass, Jobticket, Navigo, SBB GA/Halbtax |
| `bike_leasing` | JobRad, bicycle leasing, Swapfiets |
| `company_car` | Company car, car allowance, Firmenwagen |

## Architecture

```
┌──────────────────────────────────────────┐
│  Scheduler (scheduler.py)                │
│  └─ monitors discover new postings       │
│     └─ to_be_enriched = true             │
└──────────────┬───────────────────────────┘
               │
┌──────────────▼───────────────────────────┐
│  Enricher loop (enricher.py)             │
│  Phase A: collect_completed_batches()    │
│    - poll provider for finished batches  │
│    - validate results against schema     │
│    - write to job_posting.enrichment     │
│  Phase B: prepare_batch() + submit()     │
│    - claim pending postings (SKIP LOCKED)│
│    - fetch HTML from R2                  │
│    - build LLM prompts                   │
│    - submit via provider batch API       │
└──────────────┬───────────────────────────┘
               │
┌──────────────▼───────────────────────────┐
│  LLM Providers (llm_providers/)          │
│  - OpenAI Batch API (structured output)  │
│  - Anthropic Message Batches (tool use)  │
│  - Gemini Batch API (response schema)    │
└──────────────────────────────────────────┘
```

## Storage

Enrichment data is stored in `job_posting.enrichment` as JSONB:

```json
{
  "v": 2,
  "extracted_at": "2026-03-11T12:00:00+00:00",
  "seniority": "senior",
  "education": "bachelor",
  "experience": {"min": 5, "max": null},
  "visa_sponsorship": null,
  "technologies": ["Python", "PostgreSQL", "Kubernetes"],
  "keywords": ["backend engineer", "fintech", "payments"],
  "benefits": ["equity", "bonus", "flexible_hours", "education_budget"]
}
```

The `v` field tracks the schema version. When the schema changes, `ENRICH_VERSION` is bumped and `--reprocess` re-queues items below the current version.

## Fields NOT extracted (handled elsewhere)

| Data | Source |
|---|---|
| Remote/hybrid/onsite | `job_posting.location_types` (location resolver) |
| Locations | `job_posting.location_ids` (GeoNames-backed) |
| Salary | R2 extras (scraper-extracted); no DB column yet |
| Industry | `company.industry` (Wikidata-backed FK) |
| Employment type | `job_posting.employment_type` (scraper-extracted, normalized) |

## Commands

```bash
uv run enricher                     # Continuous loop
uv run enricher --once              # One cycle then exit
uv run enricher --limit 1000        # Process at most N items
uv run enricher --dry-run           # Build prompts, estimate cost, no API calls
uv run enricher --reprocess         # Re-queue items below current ENRICH_VERSION
uv run enricher --collect-only      # Only check for completed batches
```

## Configuration

Set in environment variables (see `src/config.py`):

| Variable | Default | Description |
|---|---|---|
| `ENRICH_PROVIDER` | `""` (disabled) | `openai`, `anthropic`, or `gemini` |
| `ENRICH_MODEL` | `""` | Model ID (e.g. `gpt-4o-mini`, `claude-sonnet-4-5-20250514`) |
| `ENRICH_API_KEY` | `""` | Provider API key |
| `ENRICH_BATCH_SIZE` | `500` | Max items per batch |
| `ENRICH_MIN_BATCH_SIZE` | `10` | Submit when >= this many pending |
| `ENRICH_MAX_WAIT_MINUTES` | `60` | Submit after this wait even if below min |
| `ENRICH_POLL_INTERVAL` | `300` | Seconds between poll cycles |
| `ENRICH_DAILY_SPEND_CAP_USD` | `5.0` | Daily budget limit |

## Key Files

| File | Purpose |
|---|---|
| `src/core/enrich.py` | Schema (Pydantic model), prompt, user message builder |
| `src/enricher.py` | CLI entry point, main loop |
| `src/enrich_batch.py` | Batch processor (claim, prepare, submit, collect, persist) |
| `src/core/llm_providers/` | Provider implementations (OpenAI, Anthropic, Gemini) |
| `src/core/description_store.py` | R2 read/write (HTML descriptions) |
