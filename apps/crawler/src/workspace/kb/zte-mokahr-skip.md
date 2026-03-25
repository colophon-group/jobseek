---
type: case-study
company: zte
monitor: mokahr
scraper: skip
summary: "AES-128-CBC encrypted Mokahr API required dedicated monitor with per-response decryption"
tags: [encrypted-api, aes, mokahr, chinese-ats, new-monitor, rich-monitor]
---
# ZTE — Mokahr ATS with encrypted API

## Setup
- Monitor: mokahr (new dedicated monitor)
- Scraper: skip (rich monitor returns full data)

## Problem
ZTE uses Mokahr (app.mokahr.com), a Chinese ATS. The careers pages are SPAs
that render job listings via client-side JavaScript. The DOM monitor with
`render: true` captured only the first page of jobs (38 of 209 social, 30
of 105 campus) because the SPA uses hash-fragment routing (`#/jobs?page=1`)
that the DOM monitor can't paginate through.

## Investigation

1. **DOM monitor with render** — got first-page jobs only (38+30). The SPA
   uses `#/jobs?page=N` routing which the monitor can't navigate.

2. **API discovery** — the SPA calls a POST endpoint:
   ```
   POST https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2
   Body: {"orgId":"zte","siteId":47588,"limit":20,"offset":0}
   ```

3. **Encryption** — API responses are encrypted:
   ```json
   {"data": "<base64-ciphertext>", "necromancer": "40f32df3d78ac97d"}
   ```
   - Algorithm: AES-128-CBC
   - Key: the `necromancer` field (16-char ASCII string, NOT hex-decoded)
   - IV: embedded in the SPA's `<input id="init-data">` element as `aesIv`
   - Each response has a fresh random key; the IV is fixed per site

4. **Key insight**: the `necromancer` and `aesIv` values are 16-character
   ASCII strings used directly as 128-bit keys (not hex-decoded to 8 bytes).
   Initial attempt to hex-decode them produced "Invalid key size (64) for
   AES" — the fix was `key.encode("ascii")` instead of `bytes.fromhex(key)`.

## Key decisions

- **New monitor type** vs api_sniffer wrapper: The encryption/decryption
  logic is Mokahr-specific and doesn't generalize well. A thin standalone
  monitor (190 lines) was cleaner than adding decryption hooks to
  api_sniffer.
- **Rich monitor** (scraper: skip): The list API returns title, locations
  (cityName + country), publishedAt, and commitment (employment type).
  No description in the list endpoint, but a separate detail endpoint
  exists (also encrypted) — not implemented yet.
- **Auto-detection**: `can_handle` matches `app.mokahr.com` URLs and
  extracts `org_id` and `site_id` from the URL path.
- **Campus vs social**: Mokahr uses different URL paths for campus
  (`campus-recruitment`) and social (`social-recruitment`) recruitment.
  The monitor auto-detects the path from the board URL.

## Config
```json
{
  "monitor_type": "mokahr",
  "monitor_config": {"org_id": "zte", "site_id": 47588},
  "scraper_type": "skip"
}
```

## Result
- Social board: 209 jobs (was 38 via DOM)
- Campus board: 105 jobs (was 30 via DOM)
- Total: 314 jobs with title, locations, date_posted, employment_type

## Lesson
When an ATS encrypts API responses, check:
1. Is the encryption per-response (fresh key each time) or per-session?
2. Where is the key material? (response body, headers, page HTML, cookies)
3. Is the key hex-encoded, base64, or raw ASCII? Try `.encode("ascii")`
   before `.fromhex()` — many implementations use the string directly.
4. Consider whether a dedicated monitor is justified vs. extending an
   existing one. If the decryption is ATS-specific and the data structure
   is well-defined, a standalone monitor is usually cleaner.
