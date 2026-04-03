# Typesense Benchmark Results

Measured against 658,412 real job postings from production Hetzner Postgres, running Typesense 27.1 in Docker on a local Colima VM (2GB RAM allocated).

## Performance

### Search queries (median of 3 runs)

| Query | Server time | Results |
|-------|------------|---------|
| `search("React Developer")` grouped + facet | **10ms** | 44 companies |
| `search("Senior Python Berlin")` multi-keyword | **10ms** | 93 companies |
| `search("Develoer")` typo tolerance | **11ms** | 8,822 results |
| Salary 50-100K filter | **12ms** | 28,968 results |
| Location typeahead "Ber" | **<1ms** | Berlin (4,123 postings) |
| Company typeahead "Str" | **<1ms** | STR |
| Occupation typeahead "Develop" | **<1ms** | Fullstack/Backend/Frontend Developer |
| Technology "C++" | **<1ms** | C++ (special chars work) |
| `listTopCompanies` exhaustive facet (unfiltered) | **82ms** | 3,868 companies |
| `listTopCompanies` filtered (Berlin) | **6ms** | 205 companies |
| Salary histogram (3 buckets) | **32ms** | |
| Experience histogram | **53ms** | 219,224 with experience |
| Browse-all locations (500 facets) | **101ms** | 6,454 locations |

### Comparison with Postgres

| Operation | Postgres (estimated) | Typesense | Speedup |
|-----------|---------------------|-----------|---------|
| Typeahead (suggestLocations) | 50-200ms | <1ms | **50-200x** |
| Keyword search | 100-500ms | 10ms | **10-50x** |
| Browse-all locations | 500-2000ms | 101ms | **5-20x** |
| Salary histogram | 50-200ms | 32ms | **2-6x** |

### Notable results

- **Typo tolerance works**: "Develoer" (missing 'p') returns 8,822 developer results
- **Special characters work**: "C++" correctly matches via `symbols_to_index`
- **Multi-keyword AND-first**: "Senior Python Berlin" returns 93 relevant company groups
- **Filtered facet is fast**: Unfiltered listTopCompanies (3,868 companies) takes 82ms, but filtered (Berlin only) drops to 6ms
- **Typeahead is instantaneous**: All suggest functions return in <1ms

## Memory usage

| Documents | Memory |
|-----------|--------|
| 0 (empty) | ~150 MB (base) |
| 64,500 | 184 MB |
| 239,000 | 260 MB |
| 474,000 | 340 MB |
| 658,412 | **478 MB** |

### Scaling model (linear regression)

```
Memory = 161 MB (base) + 0.48 KB per document

Projections:
    658K docs →   478 MB  (current)
  1,000K docs →   641 MB
  3,000K docs → 1,601 MB (1.6 GB)
  5,000K docs → 2,561 MB (2.5 GB)
```

### 4GB Hetzner CX22 budget

| Component | RAM |
|-----------|-----|
| OS + Docker | ~400 MB |
| Typesense (658K docs) | ~478 MB |
| Taxonomy collections (37K locations, 264 occupations, etc.) | ~30 MB |
| **Headroom** | **~3.1 GB** |

Comfortable up to ~6M postings on a 4GB box. Upgrade to 8GB at ~8M+.

## Collection statistics

| Collection | Documents | Purpose |
|------------|-----------|---------|
| job_posting | 658,412 | Primary search |
| location | 37,526 | Location typeahead + browse-all |
| company | 3,887 | Company typeahead + counts |
| occupation | 264 | Occupation typeahead (66 × 4 locales) |
| technology | 186 | Technology typeahead |
| seniority | 36 | Seniority typeahead (9 × 4 locales) |
| watchlist | 0 | Public watchlist search (populated by web app) |

## Backfill performance

- **658K docs**: ~11 minutes at ~1,000 docs/sec (over network from Hetzner)
- **Batch size**: 500 documents per API call
- **Zero errors** across full backfill

## Test suite results

### Crawler E2E tests (37 tests)
- Schema validation: 11 tests (all 7 collections, field counts, geopoint, symbols)
- Data integrity: 11 tests (sentinels, denormalization, timestamps)
- Search: 12 tests (keyword, faceting, filtering, pagination)
- Special characters: 4 tests (C++, C#, .NET)

### Web E2E tests (32 tests)
- SearchProvider: 10 tests (search, listTopCompanies, loadPostings, loadPostingsWithCounts)
- Histograms: 7 tests (salary buckets, experience excluding sentinel)
- Sentinel values: 3 tests (experience -1 included, locales _none included, salary guard)
- Graceful degradation: 4 tests (degraded:true on connection error)
- Typeahead: covered in search tests
- Watchlist: covered in search tests

All 69 tests pass with self-seeded synthetic data.
