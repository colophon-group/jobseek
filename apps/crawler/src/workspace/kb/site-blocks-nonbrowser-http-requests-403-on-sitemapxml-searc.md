---
step: add_boards
symptom: Site blocks non-browser HTTP requests (403 on sitemap.xml, search pages, job pages) but sitemap.xml is accessible from browser context
tags: ['sitemap', 'dom', 'evaluate', '403', 'browser-only', 'careersitecloud']
---
# Site blocks non-browser HTTP requests (403 on sitemap.xml, search pages, job pages) but sitemap.xml is accessible from browser context

## Problem
Site blocks non-browser HTTP requests (403 on sitemap.xml, search pages, job pages) but sitemap.xml is accessible from browser context

## Solution
Use DOM monitor with render:true and an evaluate action to fetch sitemap.xml from within the browser, parse it with DOMParser, and inject job URLs as anchor elements into the page. Use Promise chains (not await) in evaluate scripts. Example: fetch('/sitemap.xml').then(r=>r.text()).then(text=>{const parser=new DOMParser();const xml=parser.parseFromString(text,'text/xml');[...xml.querySelectorAll('url loc')].forEach(el=>{const a=document.createElement('a');a.href=el.textContent;document.body.appendChild(a);});})
