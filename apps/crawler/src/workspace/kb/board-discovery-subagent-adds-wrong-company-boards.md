---
step: add_boards
symptom: "Board discovery subagent adds boards from wrong company due to workspace contamination"
tags: [discovery, wrong-company, subagent, workspace, contamination]
---
# Board discovery subagent adds boards from wrong company

## Problem
In the parallel pipeline, a board discovery subagent may add boards belonging
to a different company if the workspace active slug gets corrupted. This
happens when multiple agents share session state and one runs `ws use
<other-slug>`, overwriting the active workspace for all agents. Symptoms:
board URLs point to a different company's career page, job titles are from the
wrong industry, and the board alias may reference an unrelated ATS tenant.

## Solution
Always pass the explicit slug in `ws add board` commands to avoid depending on
the active workspace state:
```bash
ws add board <slug> <alias> --url <url>
```

1. Verify each discovered board URL belongs to the target company by checking
   the domain and job content — open the URL and confirm job titles match the
   company's industry.

2. If erroneous boards were already added, delete them:
   ```bash
   ws del board <alias>
   ```

3. To prevent recurrence, ensure each subagent operates on an explicit slug
   rather than relying on `ws use` to set the active workspace.
