---
step: add_boards
symptom: Phenom People ATS site blocks browser navigation (timeout on Page.goto) but sitemap.xml and individual job page HTML are accessible
tags: ['phenom', 'bot-protection', 'sitemap', 'timeout']
---
# Phenom People ATS site blocks browser navigation (timeout on Page.goto) but sitemap.xml and individual job page HTML are accessible

## Problem
Phenom People ATS site blocks browser navigation (timeout on Page.goto) but sitemap.xml and individual job page HTML are accessible

## Solution
Use sitemap monitor instead of browser-based monitors. Phenom People sites typically serve sitemap.xml without bot protection even when the main page blocks headless browsers.
