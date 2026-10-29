---
step: configure
symptom: Arcis Golf Workday JSON-LD returns plausible but wrong job cities despite 100% location coverage.
tags: ['workday', 'json-ld', 'locations', 'quality']
---
# Arcis Golf Workday JSON-LD returns plausible but wrong job cities despite 100% location coverage.

## Problem
Arcis Golf Workday JSON-LD returns plausible but wrong job cities despite 100% location coverage.

## Solution
Compare identical roles against the native Workday detail API before accepting JSON-LD. Arcis JSON-LD returned Montgomery instead of Laytonsville, MD and Forest Park instead of St Louis, MO; the native workday scraper retained club, city and state plus full HTML descriptions. Keep the native scraper when its locations are more accurate.
