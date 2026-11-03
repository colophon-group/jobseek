---
step: configure
symptom: ClientConnections BMO portal advertises 66 jobs and retains JobPosting JSON-LD on 55 closed detail pages.
tags: ['clientconnections', 'dom', 'currentness', 'pagination', 'json-ld']
---
# ClientConnections BMO portal advertises 66 jobs and retains JobPosting JSON-LD on 55 closed detail pages.

## Problem
ClientConnections BMO portal advertises 66 jobs and retains JobPosting JSON-LD on 55 closed detail pages.

## Solution
Use the actual bmo-portal.clientconnections.com/jobs listing with static page pagination. Neither sitemap presence nor JobPosting JSON-LD proves currentness: inspect detail application status. DOM exclude_detail_selector targeting the observed closed-status paragraph p.MuiTypography-body1.css-nrhcsw retained 11 accepting-application roles; verify that selector is absent from current samples and recheck on markup changes. JSON-LD extracts required fields, contract type and date, but single-value hourly salary amounts and workplace mode remain limited.
