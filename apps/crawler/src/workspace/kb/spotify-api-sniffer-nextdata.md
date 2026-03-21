---
type: case-study
company: spotify
monitor: api_sniffer
scraper: nextdata
summary: "Lever nextdata with split description and each+wrap for content.lists"
tags: [lever, list-concat, each-wrap, description-split, nextdata]
---
# Spotify — Lever nextdata with split description

## Setup
- Monitor: api_sniffer (list API, HTTP POST to Lever postings endpoint)
- Scraper: nextdata (enriches description from detail pages)

## Key decisions
- Description split across `content.descriptionHtml`, `content.lists[*]`, `content.closingHtml`
- Lists contain `{text, content}` objects where `content` already includes `<ul>` tags
- Used `each+wrap` template: `<h3>{text}</h3>\n{content}` (no extra `<ul>` wrapping needed)
- Concatenated with `list_concat`: `descriptionHtml` + lists (each+wrap) + `closingHtml`
- The Lever API returns paginated results; configured `pagination.increment` to match page size

## Config
```json
{
  "scraper_config": {
    "fields": {
      "description": {
        "list_concat": [
          {"path": "content.descriptionHtml"},
          {"path": "content.lists", "each": "<h3>{text}</h3>\n{content}"},
          {"path": "content.closingHtml"}
        ]
      }
    }
  }
}
```

## Lesson
When Lever `content.lists[*].content` already contains HTML list markup (`<ul>`/`<li>`),
use `each+wrap` with just a heading — do not double-wrap with additional `<ul>` tags.
