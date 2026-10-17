package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestCandidatePlansAndIssueRenderingMatchPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_candidate_plans.json")
	if e != nil {
		t.Fatal(e)
	}
	var oracle struct {
		CompaniesCSV string `json:"companies_csv"`
		BoardsCSV    string `json:"boards_csv"`
		Items        []githubWorkItem
		Cases        []struct {
			Candidate   inventoryCandidate
			Plan        map[string]any
			Related     []localInventoryCompany `json:"related_identities"`
			Title, Body string
		}
	}
	if e = json.Unmarshal(raw, &oracle); e != nil {
		t.Fatal(e)
	}
	root := t.TempDir()
	companies, boards := filepath.Join(root, "companies.csv"), filepath.Join(root, "boards.csv")
	for path, body := range map[string]string{companies: oracle.CompaniesCSV, boards: oracle.BoardsCSV} {
		if e = os.WriteFile(path, []byte(body), 0600); e != nil {
			t.Fatal(e)
		}
	}
	local, e := loadLocalRegistryIndex(companies, boards)
	if e != nil {
		t.Fatal(e)
	}
	ledger, e := openCandidateLedger(filepath.Join(root, "ledger.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer ledger.Close()
	if _, e = ledger.reconcileRemote(context.Background(), oracle.Items); e != nil {
		t.Fatal(e)
	}
	github := newGitHubCandidateIndex(oracle.Items)
	for i, c := range oracle.Cases {
		plan := planInventoryCandidate(c.Candidate, local, github, ledger)
		if !reflect.DeepEqual(normalizedJSON(t, plan.report()), normalizedJSON(t, c.Plan)) {
			t.Fatalf("Python local/GitHub/ledger dedup case %d differs", i)
		}
		if !reflect.DeepEqual(local.relatedIdentityMatches(c.Candidate), c.Related) {
			t.Fatalf("Python local identity match case %d differs", i)
		}
		title, body, e := renderCandidateIssue(plan, 6184)
		if e != nil || title != c.Title || body != c.Body {
			t.Fatalf("Python issue renderer case %d differs: %v", i, e)
		}
	}
}
