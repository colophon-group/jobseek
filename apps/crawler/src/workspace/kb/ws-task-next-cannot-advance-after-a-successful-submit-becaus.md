---
step: submit
symptom: ws task next cannot advance after a successful submit because the submitted gate is still false
tags: ['submit', 'workflow', 'gate', 'task-next', 'pr-ready']
---
# ws task next cannot advance after a successful submit because the submitted gate is still false

## Problem
ws task next cannot advance after a successful submit because the submitted gate is still false

## Solution
Gate the submit step on Workspace.submitted checkpoint flags instead of pr_ready, because PR readiness is only set later by ws task complete.
