---
step: select_monitor
symptom: Greenhouse monitor fails with 'Cannot derive Greenhouse token from board URL' when board URL is a custom domain (e.g. company.com/careers/jobs/) instead of boards.greenhouse.io/<token>
tags: ['greenhouse', 'token', 'custom-domain', 'monitor-config']
---
# Greenhouse monitor fails with 'Cannot derive Greenhouse token from board URL' when board URL is a custom domain (e.g. company.com/careers/jobs/) instead of boards.greenhouse.io/<token>

## Problem
Greenhouse monitor fails with 'Cannot derive Greenhouse token from board URL' when board URL is a custom domain (e.g. company.com/careers/jobs/) instead of boards.greenhouse.io/<token>

## Solution
Provide the token explicitly via --config '{"token": "<token>"}'. The token can be found in the probe logs from Step 1 (look for 'greenhouse.detected_by_probe board_token=<token>').
