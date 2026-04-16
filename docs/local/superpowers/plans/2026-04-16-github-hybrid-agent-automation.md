# GitHub Hybrid Agent Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub-first, self-hosted-runner workflow to `jobseek` so labeled issues can be planned by Claude, built by Codex, reviewed in a bounded pass, and surfaced through GitHub issues/PRs.

**Architecture:** Extend the repo's existing GitHub automation patterns instead of creating a separate orchestrator. Store durable per-issue artifacts under `ai/runs/<issue>/`, implement the control flow with new `.github/workflows/*.yml` files, and keep behavior in auditable shell scripts under `.github/scripts/`.

**Tech Stack:** GitHub Actions, self-hosted macOS runner, shell (`bash`), `gh`, `jq`, local `claude` CLI, local `codex` CLI

---

## File Structure

### Create

- `.github/workflows/agent-plan.yml`
- `.github/workflows/agent-build.yml`
- `.github/workflows/agent-review.yml`
- `.github/scripts/agent-lib.sh`
- `.github/scripts/agent-plan.sh`
- `.github/scripts/agent-build.sh`
- `.github/scripts/agent-review.sh`
- `.github/ISSUE_TEMPLATE/agent-task.yml`
- `docs/14-github-hybrid-agent-automation.md`

### Modify

- `README.md`

### Runtime-created paths

- `ai/runs/<issue-number>/Goal.md`
- `ai/runs/<issue-number>/Spec.md`
- `ai/runs/<issue-number>/Plan.json`
- `ai/runs/<issue-number>/Build.md`
- `ai/runs/<issue-number>/Review.md`
- `ai/runs/<issue-number>/Status.json`

### Notes

- Implement in a fresh branch or worktree. The current worktree is dirty.
- Reuse repo conventions from `.github/scripts/select-issue.sh` and `.github/workflows/resolve-company-requests.yml`.

---

### Task 1: Add the shared shell library and runtime conventions

**Files:**
- Create: `.github/scripts/agent-lib.sh`
- Modify: `README.md`
- Test: shell syntax checks via `bash -n`

- [ ] **Step 1: Create the shared helper library**

Write `.github/scripts/agent-lib.sh` with strict mode, common env checks, runtime path helpers, and label/comment helpers.

```bash
#!/usr/bin/env bash
set -euo pipefail

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "::error::Missing required command: $1"
    exit 1
  }
}

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "::error::Missing required environment variable: $name"
    exit 1
  fi
}

issue_run_dir() {
  local issue="$1"
  printf 'ai/runs/%s\n' "$issue"
}

ensure_run_dir() {
  local dir
  dir="$(issue_run_dir "$1")"
  mkdir -p "$dir"
  printf '%s\n' "$dir"
}

status_file() {
  printf '%s/Status.json\n' "$(issue_run_dir "$1")"
}
```

- [ ] **Step 2: Add GitHub mutation helpers to the shared library**

Append helper functions for labels, comments, and issue metadata extraction.

```bash
issue_json() {
  gh issue view "$ISSUE_NUMBER" --repo "$REPO" --json number,title,body,labels,url
}

has_label() {
  local label="$1"
  issue_json | jq -e --arg label "$label" '.labels | map(.name) | index($label) != null' >/dev/null
}

replace_label() {
  local remove_label="$1"
  local add_label="$2"
  gh issue edit "$ISSUE_NUMBER" --repo "$REPO" --remove-label "$remove_label" --add-label "$add_label"
}

post_issue_comment() {
  local body_file="$1"
  gh issue comment "$ISSUE_NUMBER" --repo "$REPO" --body-file "$body_file"
}

post_pr_comment() {
  local pr_number="$1"
  local body_file="$2"
  gh pr comment "$pr_number" --repo "$REPO" --body-file "$body_file"
}

linked_issue_from_pr() {
  local pr_number="$1"
  gh pr view "$pr_number" --repo "$REPO" --json body --jq '.body' \
    | sed -nE 's/.*Closes #([0-9]+).*/\1/p' \
    | head -n1
}
```

- [ ] **Step 3: Add status writer helper to the shared library**

Append a JSON status writer so every stage records machine-readable state.

```bash
write_status() {
  local phase="$1"
  local state="$2"
  local message="$3"

  jq -n \
    --arg issue "$ISSUE_NUMBER" \
    --arg phase "$phase" \
    --arg state "$state" \
    --arg message "$message" \
    --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{
      issue: $issue,
      phase: $phase,
      state: $state,
      message: $message,
      updated_at: $updated_at
    }' > "$(status_file "$ISSUE_NUMBER")"
}
```

- [ ] **Step 4: Document the new automation entrypoint in `README.md`**

Add a short section near the repo automation/docs references:

```md
## GitHub Hybrid Agent Automation

This repo includes a personal-use GitHub-first automation loop for general engineering tasks.

- Open an issue using the `Agent task` template
- Add the `agent:plan` label to generate a plan
- After review, add `agent:build` to execute
- Review the PR on GitHub, then add `agent:review` for the bounded review pass

See `docs/14-github-hybrid-agent-automation.md` for setup and operating details.
```

- [ ] **Step 5: Run shell syntax validation**

Run: `bash -n .github/scripts/agent-lib.sh`  
Expected: no output and exit code `0`

- [ ] **Step 6: Commit the shared scaffolding**

```bash
git add .github/scripts/agent-lib.sh README.md
git commit -m "feat: add shared agent automation helpers"
```

---

### Task 2: Implement the planning stage

**Files:**
- Create: `.github/scripts/agent-plan.sh`
- Create: `.github/workflows/agent-plan.yml`
- Create: `.github/ISSUE_TEMPLATE/agent-task.yml`
- Test: `bash -n`, workflow YAML inspection

- [ ] **Step 1: Create the planning script**

Write `.github/scripts/agent-plan.sh` to create the run directory, gather issue context, invoke Claude, and persist artifacts.

```bash
#!/usr/bin/env bash
set -euo pipefail

source .github/scripts/agent-lib.sh

require_env REPO
require_env ISSUE_NUMBER
require_cmd gh
require_cmd jq
require_cmd claude

RUN_DIR="$(ensure_run_dir "$ISSUE_NUMBER")"
GOAL_FILE="$RUN_DIR/Goal.md"
SPEC_FILE="$RUN_DIR/Spec.md"
PLAN_FILE="$RUN_DIR/Plan.json"
COMMENT_FILE="$RUN_DIR/plan-comment.md"
PROMPT_FILE="$RUN_DIR/plan-prompt.md"

issue_json > "$RUN_DIR/issue.json"

jq -r '"# Goal\n\n## Issue #\(.number): \(.title)\n\n\(.body)\n"' "$RUN_DIR/issue.json" > "$GOAL_FILE"

write_status "plan" "running" "Generating plan artifacts with Claude"

cat > "$PROMPT_FILE" <<'EOF'
Read the goal and return JSON with exactly these top-level keys:
- `spec_markdown`: markdown design/spec text
- `plan_json`: object with `summary`, `execution_brief`, and `verification` array
- `summary_markdown`: short issue comment summary
EOF

cat "$GOAL_FILE" >> "$PROMPT_FILE"

claude -p "$(cat "$PROMPT_FILE")" --output-format json > "$RUN_DIR/claude-plan.json"
```

- [ ] **Step 2: Parse Claude output into spec and plan artifacts**

Extend `.github/scripts/agent-plan.sh` with deterministic extraction and issue-comment generation.

```bash
jq -r '.spec_markdown // .result.spec_markdown' "$RUN_DIR/claude-plan.json" > "$SPEC_FILE"
jq '.plan_json // .result.plan_json' "$RUN_DIR/claude-plan.json" > "$PLAN_FILE"

jq -r '.summary_markdown // .result.summary_markdown' "$RUN_DIR/claude-plan.json" > "$COMMENT_FILE"

cat >> "$COMMENT_FILE" <<EOF

- Spec: \`ai/runs/$ISSUE_NUMBER/Spec.md\`
- Plan: \`ai/runs/$ISSUE_NUMBER/Plan.json\`
- Next step: review the plan, then add the \`agent:build\` label to execute it.
EOF

post_issue_comment "$COMMENT_FILE"
replace_label "agent:plan" "agent:build-ready"
write_status "plan" "completed" "Plan artifacts generated"
```

- [ ] **Step 3: Create the planning workflow**

Write `.github/workflows/agent-plan.yml`:

```yaml
name: Agent plan

on:
  issues:
    types: [labeled]

permissions:
  contents: write
  issues: write
  pull-requests: write

jobs:
  plan:
    if: github.event.label.name == 'agent:plan'
    runs-on: [self-hosted, macOS, codex]
    steps:
      - uses: actions/checkout@v4
      - name: Run planning stage
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          ISSUE_NUMBER: ${{ github.event.issue.number }}
        run: .github/scripts/agent-plan.sh
```

- [ ] **Step 4: Add the issue intake template**

Create `.github/ISSUE_TEMPLATE/agent-task.yml`:

```yaml
name: Agent task
description: Request GitHub-hybrid planning/build/review automation for a personal engineering task
body:
  - type: textarea
    id: goal
    attributes:
      label: Goal
      description: What should the agent accomplish?
    validations:
      required: true
  - type: textarea
    id: constraints
    attributes:
      label: Constraints
      description: Non-goals, boundaries, or required approaches
  - type: textarea
    id: verification
    attributes:
      label: Verification
      description: Exact commands or checks the build stage should run
```

- [ ] **Step 5: Run validation for the planning stage**

Run:
- `bash -n .github/scripts/agent-plan.sh`
- `bash -n .github/scripts/agent-lib.sh`

Expected: no output and exit code `0`

- [ ] **Step 6: Commit the planning stage**

```bash
git add .github/scripts/agent-plan.sh .github/workflows/agent-plan.yml .github/ISSUE_TEMPLATE/agent-task.yml
git commit -m "feat: add issue-driven planning workflow"
```

---

### Task 3: Implement the build stage

**Files:**
- Create: `.github/scripts/agent-build.sh`
- Create: `.github/workflows/agent-build.yml`
- Test: `bash -n`

- [ ] **Step 1: Create the build script**

Write `.github/scripts/agent-build.sh` to enforce preconditions and derive branch state.

```bash
#!/usr/bin/env bash
set -euo pipefail

source .github/scripts/agent-lib.sh

require_env REPO
require_env ISSUE_NUMBER
require_cmd gh
require_cmd jq
require_cmd codex
require_cmd git

RUN_DIR="$(ensure_run_dir "$ISSUE_NUMBER")"
PLAN_FILE="$RUN_DIR/Plan.json"
BUILD_FILE="$RUN_DIR/Build.md"
issue_json > "$RUN_DIR/issue.json"

test -f "$PLAN_FILE" || {
  echo "::error::Missing plan file: $PLAN_FILE"
  exit 1
}

has_label "agent:build-ready" || {
  echo "::error::Issue must carry agent:build-ready before build"
  exit 1
}
```

- [ ] **Step 2: Add branch, execution, and PR logic**

Append build execution and PR creation to `.github/scripts/agent-build.sh`.

```bash
SLUG="$(jq -r '.title // "task"' "$RUN_DIR/issue.json" 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
BRANCH="agent/${ISSUE_NUMBER}-${SLUG}"

git checkout -B "$BRANCH"
write_status "build" "running" "Executing approved plan with Codex"

EXECUTION_BRIEF="$(jq -r '.execution_brief // .summary // "Implement the approved plan."' "$PLAN_FILE")"
codex exec "$EXECUTION_BRIEF"

VERIFY_CMDS="$(jq -r '.verification[]?' "$PLAN_FILE")"
if [ -z "$VERIFY_CMDS" ]; then
  echo "::error::Plan.json must provide at least one verification command"
  exit 1
fi

printf '# Build\n\n' > "$BUILD_FILE"
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  printf -- '- `%s`\n' "$cmd" >> "$BUILD_FILE"
  bash -lc "$cmd"
done <<EOF
$VERIFY_CMDS
EOF
```

- [ ] **Step 3: Add commit and PR update handling**

Append commit/push/PR code to `.github/scripts/agent-build.sh`.

```bash
if [ -n "$(git status --short)" ]; then
  git add -A
  git commit -m "feat: implement issue #$ISSUE_NUMBER plan"
  git push -u origin "$BRANCH"
fi

PR_URL="$(gh pr view "$BRANCH" --repo "$REPO" --json url --jq '.url' 2>/dev/null || true)"
if [ -z "$PR_URL" ]; then
  PR_URL="$(gh pr create --repo "$REPO" --title "Agent build: issue #$ISSUE_NUMBER" --body "Closes #$ISSUE_NUMBER" --head "$BRANCH" --base main)"
fi

write_status "build" "completed" "Build completed and PR created or updated"
printf 'Build complete.\n\nPR: %s\n\nReview the PR on GitHub, then add the `agent:review` label to the PR when you want the bounded review pass to run.\n' "$PR_URL" > "$RUN_DIR/build-comment.md"
post_issue_comment "$RUN_DIR/build-comment.md"
gh issue edit "$ISSUE_NUMBER" --repo "$REPO" --remove-label "agent:build" --remove-label "agent:build-ready"
```

- [ ] **Step 4: Create the build workflow**

Write `.github/workflows/agent-build.yml`:

```yaml
name: Agent build

on:
  issues:
    types: [labeled]

permissions:
  contents: write
  issues: write
  pull-requests: write

jobs:
  build:
    if: github.event.label.name == 'agent:build'
    runs-on: [self-hosted, macOS, codex]
    steps:
      - uses: actions/checkout@v4
      - name: Run build stage
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          ISSUE_NUMBER: ${{ github.event.issue.number }}
        run: .github/scripts/agent-build.sh
```

- [ ] **Step 5: Run validation for the build stage**

Run:
- `bash -n .github/scripts/agent-build.sh`
- `bash -n .github/scripts/agent-lib.sh`

Expected: no output and exit code `0`

- [ ] **Step 6: Commit the build stage**

```bash
git add .github/scripts/agent-build.sh .github/workflows/agent-build.yml
git commit -m "feat: add issue-driven build workflow"
```

---

### Task 4: Implement the review stage

**Files:**
- Create: `.github/scripts/agent-review.sh`
- Create: `.github/workflows/agent-review.yml`
- Create: `docs/14-github-hybrid-agent-automation.md`
- Test: `bash -n`

- [ ] **Step 1: Create the review script**

Write `.github/scripts/agent-review.sh` to gather diff context and post bounded findings.

```bash
#!/usr/bin/env bash
set -euo pipefail

source .github/scripts/agent-lib.sh

require_env REPO
require_env PR_NUMBER
require_cmd gh
require_cmd jq
require_cmd claude

ISSUE_NUMBER="$(linked_issue_from_pr "$PR_NUMBER")"
[ -n "$ISSUE_NUMBER" ] || {
  echo "::error::Unable to resolve linked issue from PR body"
  exit 1
}

RUN_DIR="$(ensure_run_dir "$ISSUE_NUMBER")"
REVIEW_FILE="$RUN_DIR/Review.md"

gh pr view "$PR_NUMBER" --repo "$REPO" --json number,title,body,files > "$RUN_DIR/pr.json"
gh pr diff "$PR_NUMBER" --repo "$REPO" > "$RUN_DIR/diff.patch"

write_status "review" "running" "Running bounded review"
```

- [ ] **Step 2: Add review generation and label updates**

Append review generation and PR commenting.

```bash
claude -p "Review the diff in ai/runs/$ISSUE_NUMBER/diff.patch for bugs, regressions, missing verification, and overreach. Return concise markdown." > "$REVIEW_FILE"
post_pr_comment "$PR_NUMBER" "$REVIEW_FILE"

if grep -qi "no findings" "$REVIEW_FILE"; then
  write_status "review" "completed" "Review passed"
  gh issue edit "$ISSUE_NUMBER" --repo "$REPO" --remove-label "agent:review" --add-label "agent:done"
else
  write_status "review" "blocked" "Review found issues"
  gh issue edit "$ISSUE_NUMBER" --repo "$REPO" --remove-label "agent:review" --add-label "agent:blocked"
fi
```

- [ ] **Step 3: Create the review workflow**

Write `.github/workflows/agent-review.yml`:

```yaml
name: Agent review

on:
  pull_request:
    types: [labeled]

permissions:
  contents: read
  issues: write
  pull-requests: write

jobs:
  review:
    if: github.event.label.name == 'agent:review'
    runs-on: [self-hosted, macOS, codex]
    steps:
      - uses: actions/checkout@v4
      - name: Run review stage
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: .github/scripts/agent-review.sh
```

- [ ] **Step 4: Write the operator documentation**

Create `docs/14-github-hybrid-agent-automation.md` with:

```md
# GitHub Hybrid Agent Automation

## Setup

1. Install a self-hosted macOS GitHub runner with label `codex`
2. Ensure `gh`, `jq`, `claude`, and `codex` are installed and authenticated
3. Create labels:
   - `agent:plan`
   - `agent:build-ready`
   - `agent:build`
   - `agent:review`
   - `agent:blocked`
   - `agent:done`

## Operating loop

1. Open an issue with the `Agent task` template
2. Add `agent:plan`
3. Review `ai/runs/<issue>/Spec.md` and `Plan.json`
4. Add `agent:build`
5. Review the PR
6. Add `agent:review` to the PR
```

- [ ] **Step 5: Run validation for the review stage**

Run:
- `bash -n .github/scripts/agent-review.sh`
- `bash -n .github/scripts/agent-lib.sh`

Expected: no output and exit code `0`

- [ ] **Step 6: Commit the review stage and docs**

```bash
git add .github/scripts/agent-review.sh .github/workflows/agent-review.yml docs/14-github-hybrid-agent-automation.md
git commit -m "feat: add bounded review workflow"
```

---

### Task 5: Verify the end-to-end automation shape

**Files:**
- Modify: `docs/14-github-hybrid-agent-automation.md` if verification reveals missing setup notes
- Test: shell syntax, workflow file presence, artifact path checks

- [ ] **Step 1: Verify all scripts parse**

Run:

```bash
bash -n .github/scripts/agent-lib.sh
bash -n .github/scripts/agent-plan.sh
bash -n .github/scripts/agent-build.sh
bash -n .github/scripts/agent-review.sh
```

Expected: no output and exit code `0`

- [ ] **Step 2: Verify workflow files exist and are readable**

Run:

```bash
ls -1 .github/workflows/agent-plan.yml .github/workflows/agent-build.yml .github/workflows/agent-review.yml
```

Expected:

```text
.github/workflows/agent-build.yml
.github/workflows/agent-plan.yml
.github/workflows/agent-review.yml
```

- [ ] **Step 3: Verify docs and template exist**

Run:

```bash
ls -1 .github/ISSUE_TEMPLATE/agent-task.yml docs/14-github-hybrid-agent-automation.md
```

Expected:

```text
.github/ISSUE_TEMPLATE/agent-task.yml
docs/14-github-hybrid-agent-automation.md
```

- [ ] **Step 4: Record known limitations in the operator doc**

Add a short limitations section if it is not already present:

```md
## Current limitations

- Private repos only
- Self-hosted runner must be online
- Build runs only after explicit user relabeling
- No auto-merge
- No comment-command support in v1
```

- [ ] **Step 5: Commit final verification/doc cleanup**

```bash
git add .github/ISSUE_TEMPLATE/agent-task.yml docs/14-github-hybrid-agent-automation.md .github/workflows/agent-plan.yml .github/workflows/agent-build.yml .github/workflows/agent-review.yml .github/scripts/agent-lib.sh .github/scripts/agent-plan.sh .github/scripts/agent-build.sh .github/scripts/agent-review.sh README.md
git commit -m "docs: finalize github hybrid agent automation setup"
```

---

## Plan Self-Review

### Spec coverage

The plan covers the approved design elements:

- GitHub as control plane
- self-hosted Mac runner
- Claude planning
- Codex build
- bounded review
- durable `ai/runs` state
- repo-local docs and issue template

### Placeholder scan

There are no `TODO`, `TBD`, or “implement later” instructions inside executable tasks. Deferred features are explicitly excluded from scope.

### Type and naming consistency

The plan consistently uses:

- issue labels: `agent:plan`, `agent:build-ready`, `agent:build`, `agent:blocked`, `agent:done`
- PR label: `agent:review`
- artifact root: `ai/runs/<issue-number>/`
- script root: `.github/scripts/`
- workflow root: `.github/workflows/`
