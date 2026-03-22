# Track A: Company Metadata Enrichment

Workspace: `{{ slug }}` | Issue: #{{ issue }}
Website: {{ website }}
{% if company_name %}Company: {{ company_name }}{% endif %}


## Goal

Fill all company metadata fields so the workspace is ready for submit.
This runs in parallel with logo discovery and board configuration — do not
wait for or depend on those tracks.

## Required fields

All fields below must be set before submit. Research the company online
(official website, Wikipedia, press releases) to find accurate data.

### Descriptions (4 locales)

Write a concise, factual 1-2 sentence description of the company for each
locale. Describe what the company does, not marketing copy.

```bash
ws set {{ slug }} --description "..." --description-locale en --no-discover
ws set {{ slug }} --description "..." --description-locale de --no-discover
ws set {{ slug }} --description "..." --description-locale fr --no-discover
ws set {{ slug }} --description "..." --description-locale it --no-discover
```

Use `--no-discover` to skip logo discovery side effects.

### Industry

Run `ws help industries` to find the correct industry ID from the taxonomy.

```bash
ws set {{ slug }} --industry <id> --no-discover
```

### Employee count range

Bucket scale: 1=1-10, 2=11-50, 3=51-200, 4=201-500, 5=501-1000,
6=1001-5000, 7=5001-10000, 8=10001+

```bash
ws set {{ slug }} --employee-count-range <1-8> --no-discover
```

### Founded year

```bash
ws set {{ slug }} --founded-year <YYYY> --no-discover
```

## Guidelines

- Descriptions must be factual and locale-appropriate (translate, don't
  just copy the English version to other locales)
- Use the official company name and spelling
- If the company is a subsidiary, describe the subsidiary, not the parent
- If data is uncertain, use the most reliable source (official site > Wikipedia > press)
- If a field is genuinely unavailable (e.g., founded year for a stealth startup),
  skip it — it's optional
