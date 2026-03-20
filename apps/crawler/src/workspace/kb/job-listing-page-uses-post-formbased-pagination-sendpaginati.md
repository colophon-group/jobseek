---
step: select_monitor
symptom: Job listing page uses POST form-based pagination (sendPagination/form submit), only showing 6-10 jobs per page. DOM monitor repeat action fails because form submit navigates away.
tags: ['pagination', 'form-post', 'limit', 'get-params']
---
# Job listing page uses POST form-based pagination (sendPagination/form submit), only showing 6-10 jobs per page. DOM monitor repeat action fails because form submit navigates away.

## Problem
Job listing page uses POST form-based pagination (sendPagination/form submit), only showing 6-10 jobs per page. DOM monitor repeat action fails because form submit navigates away.

## Solution
Check if the page also accepts GET query parameters (offset, limit). Many POST forms also work with GET params. Try adding limit=200 (or similar high value) to the board URL to load all jobs in a single page, avoiding pagination entirely.
