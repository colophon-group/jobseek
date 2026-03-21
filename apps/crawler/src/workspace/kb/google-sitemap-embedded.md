---
type: case-study
company: google
monitor: sitemap
scraper: embedded
summary: "AF_initDataCallback pattern extraction with positional array indices"
tags: [embedded, af-initdatacallback, array-index, regex-pattern, sitemap, large-board]
---
# Google — AF_initDataCallback embedded data with array indices

## Setup
- Monitor: sitemap (XML sitemap with ~4000 job URLs)
- Scraper: embedded (regex pattern extraction from Google's proprietary JS data format)

## Key decisions
- Google career pages don't use standard JSON-LD or Next.js data — job data is embedded in
  `AF_initDataCallback` JavaScript blocks with a `key: 'ds:0'` identifier
- Data is structured as nested arrays, not objects — fields are accessed by positional index
  rather than named keys (e.g., `[1]` for title, `[10][1]` for description)
- Used `pattern` regex to match the callback opening and extract the data payload
- Array wildcard `[9][*][0]` extracts all location names from the locations array
- Separate fields for `qualifications` and `responsibilities` (indices `[4][1]` and `[3][1]`)
  rather than concatenating into description

## Config
```json
{
  "scraper_config": {
    "pattern": "AF_initDataCallback\\(\\{key:\\s*'ds:0'.*?data:",
    "path": "[0]",
    "fields": {
      "title": "[1]",
      "description": "[10][1]",
      "locations": "[9][*][0]",
      "qualifications": "[4][1]",
      "responsibilities": "[3][1]"
    }
  }
}
```

## Lesson
When a site uses proprietary JavaScript data embedding (like Google's AF_initDataCallback),
the `embedded` scraper with a `pattern` regex can extract the data. Use positional array
indices (`[0]`, `[1]`, etc.) when the data is arrays-of-arrays rather than objects. Discover
the correct indices by examining the raw page source in browser devtools.
