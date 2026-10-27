---
step: configure
symptom: LiveHire Halliburton probe ranked footer navigation above real jobs; static DOM found only 10 of 33 jobs and numeric query pagination repeated tracked URLs.
tags: ['livehire', 'dom', 'pagination', 'probe-false-positive', 'json-ld']
---
# LiveHire Halliburton probe ranked footer navigation above real jobs; static DOM found only 10 of 33 jobs and numeric query pagination repeated tracked URLs.

## Problem
LiveHire Halliburton probe ranked footer navigation above real jobs; static DOM found only 10 of 33 jobs and numeric query pagination repeated tracked URLs.

## Solution
Verify suggestions against actual job inventory. The official LiveHire board uses #show-more-button; rendered DOM with repeat clicks to selector disappearance discovers all 33 roles. Filter /careers/halliburton/job/ and strip query strings before URL comparison. Static JSON-LD on three detail pages yielded full descriptions, locations, contract employment types and dates. None/lean/auto browser runs had exact 33-URL parity; resource block telemetry was unavailable, so bandwidth savings remain unverified.
