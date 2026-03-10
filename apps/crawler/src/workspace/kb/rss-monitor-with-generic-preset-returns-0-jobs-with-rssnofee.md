---
step: select_monitor
symptom: RSS monitor with generic preset returns 0 jobs with 'rss.no_feed_url' error on Teamtailor boards
tags: ['rss', 'teamtailor', 'preset', 'no_feed_url']
---
# RSS monitor with generic preset returns 0 jobs with 'rss.no_feed_url' error on Teamtailor boards

## Problem
RSS monitor with generic preset returns 0 jobs with 'rss.no_feed_url' error on Teamtailor boards

## Solution
Use --config '{"preset": "teamtailor"}' to enable Teamtailor-specific feed URL discovery. The generic preset cannot auto-detect Teamtailor RSS feeds.
