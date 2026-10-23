package main

import (
	"context"
	"encoding/json"
	"errors"
	"sort"
)

type candidateIssueAction struct {
	SourceKey   string        `json:"source_key"`
	Action      string        `json:"action"`
	Plan        candidatePlan `json:"-"`
	IssueNumber *int          `json:"issue_number"`
	IssueURL    *string       `json:"issue_url"`
}

func (a candidateIssueAction) report() map[string]any {
	return map[string]any{"source_key": a.SourceKey, "action": a.Action, "plan": a.Plan.report(), "issue_number": a.IssueNumber, "issue_url": a.IssueURL}
}
func (a candidateIssueAction) MarshalJSON() ([]byte, error) { return json.Marshal(a.report()) }

type candidateIssueCoordinator struct {
	Client         *inventoryGitHubClient
	Local          *localRegistryIndex
	Ledger         *candidateLedger
	GitHub         *githubCandidateIndex
	Reconciliation ledgerReconciliation
}

func bootstrapCandidateCoordinator(ctx context.Context, client *inventoryGitHubClient, local *localRegistryIndex, ledger *candidateLedger, items []githubWorkItem) (*candidateIssueCoordinator, error) {
	var e error
	if items == nil {
		items, e = client.listCandidateWorkItems(ctx)
		if e != nil {
			return nil, e
		}
	}
	reconciliation, e := ledger.reconcileRemote(ctx, items)
	if e != nil {
		return nil, e
	}
	return &candidateIssueCoordinator{client, local, ledger, newGitHubCandidateIndex(items), reconciliation}, nil
}
func (c *candidateIssueCoordinator) plan(candidate inventoryCandidate) candidatePlan {
	return planInventoryCandidate(candidate, c.Local, c.GitHub, c.Ledger)
}
func (c *candidateIssueCoordinator) create(ctx context.Context, candidate inventoryCandidate, dryRun bool) (candidateIssueAction, error) {
	plan := c.plan(candidate)
	action := candidateIssueAction{SourceKey: candidate.SourceKey, Plan: plan}
	if !plan.eligible() {
		action.Action = "hard_skip"
		return action, nil
	}
	if dryRun {
		action.Action = "would_create"
		return action, nil
	}
	title, body, e := renderCandidateIssue(plan, 6184)
	if e != nil {
		return action, e
	}
	created, e := c.Client.createIssue(ctx, title, body, []string{"company-request", atsInventoryLabel})
	if errors.Is(e, errGitHubUnknownCreate) {
		items, readErr := c.Client.listCandidateWorkItems(ctx)
		if readErr != nil {
			return action, readErr
		}
		reconciliation, readErr := c.Ledger.reconcileRemote(ctx, items)
		if readErr != nil {
			return action, readErr
		}
		c.Reconciliation = reconciliation
		c.GitHub = newGitHubCandidateIndex(items)
		matches := []githubWorkItem{}
		for _, item := range c.GitHub.SourceHashes[candidate.sourceHash()] {
			if item.Kind == "issue" {
				matches = append(matches, item)
			}
		}
		if len(matches) == 0 {
			return action, e
		}
		sort.SliceStable(matches, func(i, j int) bool { return matches[i].Number < matches[j].Number })
		item := matches[0]
		if e = c.Ledger.recordCreated(ctx, candidate, item); e != nil {
			return action, e
		}
		action.Action = "created_reconciled"
		action.IssueNumber = &item.Number
		action.IssueURL = &item.URL
		return action, nil
	}
	if e != nil {
		return action, e
	}
	item := githubWorkItem{Kind: "issue", Number: created.Number, State: "open", Title: title, Body: body, URL: created.URL, Labels: []string{}}
	if e = c.Ledger.recordCreated(ctx, candidate, item); e != nil {
		return action, e
	}
	c.GitHub.add(item)
	action.Action = "created"
	action.IssueNumber = &created.Number
	action.IssueURL = &created.URL
	return action, nil
}

type supportIssueAction struct {
	Family                string  `json:"family"`
	Action                string  `json:"action"`
	TenantRows            int     `json:"tenant_rows"`
	JobRows               *int    `json:"job_rows"`
	IssueNumber           *int    `json:"issue_number"`
	IssueURL              *string `json:"issue_url"`
	DuplicateIssueNumbers []int   `json:"duplicate_issue_numbers"`
}

func supportJobRows(manifest map[string]any, family string) *int {
	byATS, ok := manifest["by_ats"].(map[string]any)
	if !ok {
		return nil
	}
	artifact, ok := byATS[family].(map[string]any)
	if !ok {
		return nil
	}
	n, e := impactInt(artifact, "rows")
	if e != nil {
		return nil
	}
	return &n
}
func sortInventoryRepresentatives(rows []inventoryRow) {
	sort.SliceStable(rows, func(i, j int) bool {
		a, b := rows[i], rows[j]
		if unicodeFold(a.Name) != unicodeFold(b.Name) {
			return unicodeFold(a.Name) < unicodeFold(b.Name)
		}
		return a.URL < b.URL
	})
}
func reconcileSupportIssues(ctx context.Context, snapshot inventorySnapshot, client *inventoryGitHubClient, create bool) ([]supportIssueAction, error) {
	actions := []supportIssueAction{}
	if len(snapshot.Coverage.UnsupportedFamilies) == 0 {
		return actions, nil
	}
	existing, e := client.listSupportIssues(ctx)
	if e != nil {
		return nil, e
	}
	byFamily := supportIssuesByFamily(existing)
	for _, family := range snapshot.Coverage.UnsupportedFamilies {
		matching := byFamily[family]
		action := supportIssueAction{Family: family, TenantRows: snapshot.FamilyCounts[family], JobRows: supportJobRows(snapshot.Manifest, family), DuplicateIssueNumbers: []int{}}
		if len(matching) > 0 {
			primary := matching[0]
			action.Action = "closed_existing"
			if primary.State == "open" {
				action.Action = "open_existing"
			}
			if len(matching) > 1 {
				action.Action = "duplicate_existing"
			}
			action.IssueNumber = &primary.Number
			action.IssueURL = &primary.URL
			for _, item := range matching[1:] {
				action.DuplicateIssueNumbers = append(action.DuplicateIssueNumbers, item.Number)
			}
			actions = append(actions, action)
			continue
		}
		if !create {
			action.Action = "would_create"
			actions = append(actions, action)
			continue
		}
		title, body, e := renderSupportIssue(snapshot, family)
		if e != nil {
			return nil, e
		}
		created, e := client.createIssue(ctx, title, body, []string{"enhancement", "type:feature", "area:crawler"})
		if errors.Is(e, errGitHubUnknownCreate) {
			recovered, readErr := client.listSupportIssues(ctx)
			if readErr != nil {
				return nil, readErr
			}
			matches := supportIssuesByFamily(recovered)[family]
			if len(matches) == 0 {
				return nil, e
			}
			primary := matches[0]
			action.Action = "created_reconciled"
			action.IssueNumber = &primary.Number
			action.IssueURL = &primary.URL
		} else if e != nil {
			return nil, e
		} else {
			action.Action = "created"
			action.IssueNumber = &created.Number
			action.IssueURL = &created.URL
		}
		actions = append(actions, action)
	}
	return actions, nil
}
