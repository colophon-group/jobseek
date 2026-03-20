---
step: add_boards
symptom: Phenom People career frontend (careers.abb-style) is too JS-heavy for link detection, auto-inference fails with 0 matched job links
tags: ['phenom', 'workday', 'js-heavy', 'link-detection']
---
# Phenom People career frontend (careers.abb-style) is too JS-heavy for link detection, auto-inference fails with 0 matched job links

## Problem
Phenom People career frontend (careers.abb-style) is too JS-heavy for link detection, auto-inference fails with 0 matched job links

## Solution
Use the underlying Workday board URL directly (company.wd3.myworkdayjobs.com/Site_Name). Job apply links on Phenom pages point to Workday, revealing the backend ATS URL.
