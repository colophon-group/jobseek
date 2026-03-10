---
step: add_boards
symptom: Discovery suggests www.example.com/careers as board URL but it is a marketing landing page (no job links, only informational content). Actual job listings are on a separate subdomain like careers.example.com hosted on SuccessFactors.
tags: ['successfactors', 'landing-page', 'board-url', 'subdomain']
---
# Discovery suggests www.example.com/careers as board URL but it is a marketing landing page (no job links, only informational content). Actual job listings are on a separate subdomain like careers.example.com hosted on SuccessFactors.

## Problem
Discovery suggests www.example.com/careers as board URL but it is a marketing landing page (no job links, only informational content). Actual job listings are on a separate subdomain like careers.example.com hosted on SuccessFactors.

## Solution
Check outgoing links from the careers landing page for external ATS subdomains (e.g., careers.example.com). Add the ATS subdomain as the board URL instead. SuccessFactors boards are often hosted on a careers.* subdomain separate from the main site.
