# Typesense Benchmarks

## Production — Hetzner CX22 (4GB RAM, 2 vCPU)

Measured via Cloudflare Tunnel from Zurich. 681K job postings indexed with ancestor location/occupation IDs. Times are Typesense server-side (`search_time_ms`), median of 3 runs. Network overhead (~15-30ms) is additional.

### Search queries

| Query | Server time | Results |
|-------|------------|---------|
| `search("React Developer")` grouped + facet | **27ms** | 42 companies |
| `search("Senior Python Berlin")` multi-keyword | **26ms** | 93 companies |
| `search("Develoer")` typo tolerance | **31ms** | 8,789 results |
| Salary 50-100K filter | **39ms** | 29,012 results |
| Location typeahead "Ber" | **4ms** | Berlin (top) |
| Company typeahead "Goo" | **2ms** | Google (top) |
| Occupation typeahead "Develop" | **1ms** | Fullstack/Backend/Frontend Developer |
| Technology "C++" | **<1ms** | C++ |
| `listTopCompanies` exhaustive facet (unfiltered) | **180ms** | 3,865 companies |
| `listTopCompanies` filtered (Germany) | **31ms** | 696 companies |
| Experience histogram | **109ms** | 221,819 with experience |
| Browse-all locations (500 facets) | **270ms** | 7,038 locations |
| Germany filter (ancestor, single ID) | **26ms** | 13,656 results |

### Observations

- **Typeahead**: 1-4ms — effectively instant for search-as-you-type
- **Keyword search**: 26-31ms with grouping, faceting, and typo tolerance
- **Filtered facet (Germany)**: 31ms — ancestor IDs eliminate the need for 500+ child ID expansion
- **Unfiltered facet**: 180ms — the most expensive query (exhaustive facet over 3,865 companies). Cached for 60s in production.
- **Browse-all locations**: 270ms — exhaustive facet over 7,038 locations. Cached for 1h.
- **Special characters**: C++ search works via `symbols_to_index`

### Memory

| Metric | Value |
|--------|-------|
| Typesense container | **425 MB** / 3.73 GB available |
| Job postings | 680,946 documents |
| All collections | 681K + 37K locations + 3.9K companies + 264 occupations + 186 technologies + 36 seniorities + 11 watchlists |
| Per-document (with ancestors) | ~0.6 KB |
| CPU idle | 0.7% |

### Scaling projection

```
Memory ≈ 160 MB (base) + 0.6 KB per job posting document

  681K docs →   425 MB (current)
1,000K docs →   760 MB
3,000K docs → 1,960 MB (1.9 GB)
5,000K docs → 3,160 MB (3.1 GB) — approaching 4GB box limit
```

The 4GB box is comfortable up to ~3M postings. At 5M+, upgrade to 8GB.

### Backfill performance

- **681K docs**: ~20 min via Cloudflare Tunnel (limited by tunnel throughput)
- **681K docs**: ~11 min direct (private network, from crawler box)
- **Batch size**: 500 documents per API call
- **Zero errors** across full backfill

### Comparison with Postgres

| Operation | Postgres (estimated) | Typesense | Speedup |
|-----------|---------------------|-----------|---------|
| Typeahead (suggestLocations) | 50-200ms | 1-4ms | **50-200x** |
| Keyword search | 100-500ms | 26-31ms | **4-15x** |
| Browse-all locations | 500-2000ms | 270ms | **2-7x** |
| Salary histogram | 50-200ms | ~40ms | **1-5x** |
| Country filter (Germany) | 100-300ms (with expansion) | 26ms (ancestor ID) | **4-10x** |
