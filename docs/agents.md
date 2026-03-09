# Agent Decision Mindset

This document defines how crawler setup agents should reason during `ws task` workflows.

## Core principle

`ws` output is evidence, not authority.

Agent decisions should be based on:

1. What was observed (counts, sample content, page/link evidence, API responses)
2. How that observation was obtained (static HTML, rendered DOM, monitor probe, blind guess)
3. What it likely means (primary board, stale board, partial extraction, noisy data)

The agent should avoid defaulting to rule-like "next command" nudges when evidence conflicts.

## Practical interpretation rules

- Treat monitor/scraper detections as hypotheses to validate, not instructions to accept.
- Prefer directly referenced board URLs over unreferenced slug guesses.
- Prefer explanations in notes/feedback ("what we saw + why we chose this") over command transcripts.
- Keep options open when uncertainty is high; gather one more piece of evidence before locking config.
- If two candidates disagree, explain the conflict and why one signal is stronger.

## Communication style

When presenting findings, use this structure:

1. Observation
2. Method/source of observation
3. Likely interpretation
4. Remaining uncertainty (if any)

Avoid phrasing that removes agent judgment (for example, "must use X because probe said so")
unless a hard technical constraint is proven.
