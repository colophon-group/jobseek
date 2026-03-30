# Batch-Add Company Instructions

Instructions for subagents configuring companies in bulk using the `ws` workspace CLI.

## Scope

You are configuring **one company** at a time. Your job:
1. Create the workspace and set company metadata (name, website, logos, descriptions, industry)
2. Add the board and select/test the monitor
3. Select/test the scraper
4. Record feedback and submit

---

## Phase 1: Create Workspace & Set Metadata

### 1.1 Create workspace

```bash
ws new <slug> --issue <N>
```

The slug must be lowercase alphanumeric with hyphens (e.g., `acme-corp`).

### 1.2 Set name and website

```bash
ws set --name "<Company Name>" --website "<homepage URL>"
```

This triggers auto-discovery of logo candidates and auto-enrichment (descriptions, industry, employee count, founded year). **Read the output carefully** — it tells you what was found and what's missing.

### 1.3 Select logos

After `ws set --website`, the tool discovers logo candidates and saves them as PNG previews.

**You MUST visually inspect every candidate** by reading the PNG files. The tool cannot evaluate brand correctness.

```bash
# View candidates
ws logos
```

Then select by candidate number:
```bash
ws set --logo-candidate <N> --icon-candidate <N> --logo-type <type>
```

Or provide direct URLs to known-good images:
```bash
ws set --logo-url "<url>" --icon-url "<url>" --logo-type <type>
```

After selection, **verify the final artifacts** by reading:
- `artifacts/company/logo.png` — the full logo
- `artifacts/company/icon.png` — the square icon

#### Logo Selection Rules

| Field | What it is | What to look for |
|-------|-----------|-----------------|
| `logo_url` | Full primary logo (wordmark/lockup) | The version used on the company's homepage header or press kit. Prefer SVG/PNG with transparent background. |
| `icon_url` | Minified square icon | Favicon, app icon, or logomark. Must be recognizable at small sizes. Prefer square aspect ratio. |
| `logo_type` | Classifies the **full logo** | See table below |

#### logo_type values

| Value | Meaning | Example |
|-------|---------|---------|
| `wordmark` | Text only, no symbol | "Netflix", "Google" |
| `wordmark+icon` | Text + symbol combined | GitHub (octocat + text) |
| `icon` | Symbol/icon only, no text | Apple logo, Twitter bird |

#### Logo Failure Modes (conservative)

- **No good full-logo candidate?** → Skip logo selection entirely. Leave `logo_url` empty. Submit will warn but not block.
- **No good icon candidate?** → Same — leave `icon_url` empty.
- **Unsure if image is the real brand logo?** → Do NOT select it. A missing logo is better than a wrong one.
- **Image is a banner, hero image, team photo, or product screenshot?** → Reject it.
- **Image has a colored/non-transparent background?** → Acceptable as fallback, but prefer transparent if another candidate has it.
- **SVG conversion failed?** → Use the original file; CI handles conversion.

### 1.4 Descriptions

Descriptions are required in **all four locales**: `en`, `de`, `fr`, `it`.

Auto-enrichment typically fills English only. You must provide the remaining three.

```bash
ws set --description "<English description>"
ws set --description "<German description>" --description-locale de
ws set --description "<French description>" --description-locale fr
ws set --description "<Italian description>" --description-locale it
```

#### Description Guidelines

**Format**: One sentence, 10-25 words. Describes what the company does, not marketing fluff.

**Good examples** (from existing companies):
- "Cloud platform for web scraping, browser automation, and data extraction"
- "Cyber protection platform combining backup, disaster recovery, and endpoint security"
- "Global technology company offering e-commerce, cloud computing, and AI services"
- "AI-powered tools for semiconductor chip design and verification"

**Bad examples**:
- "We're building the future of AI" — marketing, not descriptive
- "Anthropic is a company" — too vague
- "Leading global provider of innovative solutions for enterprise digital transformation" — buzzword soup
- Copy-pasting the company's full "About" page — too long

**Rules**:
1. Start with what the company IS or DOES (no "Founded in..." or "Based in...")
2. Be specific about the domain (not just "technology company" if you can say "barcode scanning and computer vision platform")
3. No superlatives ("leading", "world-class", "innovative")
4. Translations must be natural, not Google Translate artifacts. Use proper German/French/Italian business terminology.
5. Keep all four locales at roughly the same length and content

#### Description Failure Modes

- **Cannot determine what the company does?** → Search the web. Check their homepage, LinkedIn, Crunchbase.
- **Auto-enrichment returned a description but it's wrong/generic?** → Override it with `ws set --description "..."`.
- **Unsure about translation quality?** → Write a simple, concrete English description. Simpler sentences translate better.

### 1.5 Industry

Must be an integer ID from the table below:

| ID | Industry |
|----|----------|
| 1 | Technology |
| 2 | Financial Services |
| 3 | Healthcare |
| 4 | Manufacturing |
| 5 | Retail & E-commerce |
| 6 | Media & Entertainment |
| 7 | Telecommunications |
| 8 | Energy |
| 9 | Transportation & Logistics |
| 10 | Education |
| 11 | Real Estate & Construction |
| 12 | Professional Services |
| 13 | Government & Public Sector |
| 14 | Agriculture & Food |
| 15 | Aerospace & Defense |
| 16 | Automotive |
| 17 | Hospitality & Tourism |
| 18 | Pharmaceuticals & Biotech |
| 19 | Non-profit |
| 20 | Robotics |
| 21 | Cybersecurity |
| 22 | Luxury Goods |

```bash
ws set --industry <ID>
```

**Selection rules**:
- Pick the **primary** industry. A fintech company is `2` (Financial Services), not `1` (Technology).
- A biotech/pharma company doing drug discovery is `18`, not `1`.
- A consulting firm (McKinsey, Bain, KPMG) is `12` (Professional Services).
- A robotics company is `20`, even if they use AI heavily.
- A cybersecurity company is `21`, not `1`.
- A luxury watchmaker is `22`, not `4` (Manufacturing).
- When genuinely ambiguous, prefer the more specific industry over "Technology".

### 1.6 Employee Count Range

Optional but preferred. Integer 1-8:

| Bucket | Range |
|--------|-------|
| 1 | 1-10 |
| 2 | 11-50 |
| 3 | 51-200 |
| 4 | 201-500 |
| 5 | 501-1,000 |
| 6 | 1,001-5,000 |
| 7 | 5,001-10,000 |
| 8 | 10,001+ |

```bash
ws set --employee-count-range <N>
```

**If unsure, omit it.** Do not guess. Auto-enrichment from Wikidata often fills this.

### 1.7 Founded Year

Optional. Integer YYYY.

```bash
ws set --founded-year <YYYY>
```

**If unsure, omit it.** Auto-enrichment often fills this.

---

## Phase 2: Add Board & Configure Monitor

### 2.1 Add the board

```bash
ws add board <alias> --url <board-url>
```

The alias is a short identifier (e.g., `careers`, `careers-gh`, `careers-workday`). The board slug will be `<company-slug>-<alias>`.

### 2.2 Probe monitors

```bash
ws probe monitor -n <expected-job-count>
```

The `-n` flag is your best estimate of jobs visible on the careers page. This helps evaluate which monitor found the right number.

### 2.3 Select and test monitor

For the easy ATS types, monitor selection is straightforward:

```bash
# Greenhouse
ws select monitor greenhouse

# Lever
ws select monitor lever

# Ashby
ws select monitor ashby

# Recruitee
ws select monitor recruitee

# Personio
ws select monitor personio

# Gem
ws select monitor gem

# Workday (usually auto-detected from probe)
ws select monitor workday

# Rippling
ws select monitor rippling
```

Then test:
```bash
ws run monitor
```

Verify the job count is reasonable (matches what you'd expect from the careers page).

---

## Phase 3: Configure Scraper

### 3.1 Probe scrapers

```bash
ws probe scraper
```

### 3.2 Select and test scraper

For API-based monitors (Greenhouse, Lever, Ashby, Recruitee, Workday, Rippling, Gem), the monitor often embeds all job data. In that case, the scraper is typically `skip` or auto-selected.

```bash
ws select scraper <type>
ws run scraper
```

**If the monitor already returns full job content** (title, description, locations), scraper probe will show this. Select the scraper that extracts the most complete data.

### 3.3 Verify extraction quality

After `ws run scraper`, check the output for:
- **title**: Present and correct?
- **description**: HTML content, not empty/truncated?
- **locations**: Parsed correctly (city names, not raw JSON)?
- **employment_type**: Valid values if present?
- **date_posted**: ISO format if present?

---

## Phase 4: Feedback & Submit

### 4.1 Record feedback

```bash
ws feedback --verdict good
```

Verdicts: `good` (clean extraction), `acceptable` (minor issues), `poor` (significant issues), `unusable` (broken).

**Be honest with the verdict.** `good` means fields extract correctly with proper formatting. `acceptable` means it works but some optional fields are missing or slightly off.

### 4.2 Submit

```bash
ws submit --summary "<brief notes>"
```

Summary format: `"Straightforward <monitor_type> config, N jobs"` for easy cases.

---

## Verification Checklist (must pass before submit)

- [ ] Company name is correct (proper capitalization, official name)
- [ ] Website URL loads and is the correct homepage
- [ ] Logo and icon visually verified as brand-correct (or intentionally left empty)
- [ ] `logo_type` matches the selected full logo
- [ ] English description is accurate and specific
- [ ] All four locale descriptions are filled and natural-sounding
- [ ] Industry ID is correct
- [ ] Monitor returns a reasonable job count (>0, matches expectations)
- [ ] Scraper extracts title + description + locations correctly
- [ ] Feedback verdict is honest

## Conservative Failure Modes

**When in doubt, fail safe:**

| Situation | Action |
|-----------|--------|
| Cannot find the company's careers page | Stop. Report the issue — do not guess URLs. |
| Monitor returns 0 jobs | Try `ws probe monitor` again. If still 0, the board URL may be wrong. |
| Logo candidates are all wrong | Leave logos empty. Submit will warn but not block. |
| Cannot determine industry | Set to `1` (Technology) only if it's genuinely a tech company. Otherwise, search the web for the company's sector. |
| Description auto-enrichment failed | Write it manually. This is required — you cannot skip it. |
| Unsure about a translation | Write a simpler English description that translates more reliably. |
| Monitor/scraper type not in the easy list | Stop. Report back that the company needs manual configuration. |
| Board URL is behind login/auth | Stop. Report as unconfigurable with current tools. |
| Multiple career boards for one company | Configure the **primary** one. Note the others in submit summary. |
| Company appears to have no open positions | Still configure it. The monitor will simply return 0 jobs, and the scheduler will check periodically. |
