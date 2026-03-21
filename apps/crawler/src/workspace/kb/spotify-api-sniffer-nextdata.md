---
type: case-study
company: spotify
monitor: api_sniffer
scraper: nextdata
summary: "Lever nextdata with split description and each+wrap for content.lists"
tags: [lever, list-spec, each-wrap, description-split, nextdata, enrich]
---
# Spotify — Lever nextdata with split description

## Setup
- Monitor: api_sniffer (list API via Lever postings endpoint)
- Scraper: nextdata with `enrich: ["description"]` (enriches description from detail pages)

## Key decisions
- Description split across `content.descriptionHtml`, `content.lists[*]`, `content.closingHtml`
- Lists contain `{text, content}` objects where `content` already includes `<ul>` tags
- Used `each+wrap` template: `<h3>{text}</h3>\n{content}` (no extra `<ul>` wrapping needed)
- Concatenated via list spec (array as field value): three specs joined with newline
- `enrich: ["description"]` on scraper config — monitor provides titles/locations,
  scraper only fetches descriptions from detail pages

## Config
```json
{
  "scraper_config": {
    "enrich": ["description"],
    "path": "props.pageProps.job",
    "fields": {
      "title": "text",
      "description": [
        "content.descriptionHtml",
        {"each": "content.lists[*]", "wrap": "<h3>{text}</h3>\n{content}"},
        "content.closingHtml"
      ],
      "locations": "categories.allLocations",
      "employment_type": "categories.commitment"
    }
  }
}
```

## Lesson
When Lever `content.lists[*].content` already contains HTML list markup (`<ul>`/`<li>`),
use `each+wrap` with just a heading — do not double-wrap with additional `<ul>` tags.
List concatenation uses a plain array as the field value (not a `list_concat` wrapper).
