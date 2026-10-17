package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

type inventoryCLIFeed struct {
	oracle             sourceOracle
	generation         int
	items              []map[string]any
	posts              int
	unknownAfterCommit bool
	rateLimited        bool
	mu                 sync.Mutex
}

func (f *inventoryCLIFeed) Do(request *http.Request) (*http.Response, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	status := 200
	headers := http.Header{}
	var raw []byte
	item := f.oracle.Fixtures[f.generation]
	switch request.URL.Hostname() {
	case "storage.stapply.ai":
		if request.URL.String() == defaultManifestURL {
			headers.Set("ETag", item.ETag)
			raw = []byte(item.Manifest)
			if request.Header.Get("If-None-Match") == item.ETag {
				status = 304
				raw = nil
			}
		} else {
			raw = []byte(item.CSV)
		}
	case "api.github.com":
		if request.Header.Get("Authorization") != "Bearer test-ephemeral-token" {
			return nil, fmt.Errorf("expected isolated fake credential")
		}
		headers.Set("X-RateLimit-Remaining", "5000")
		headers.Set("X-RateLimit-Reset", "1792278000")
		if f.rateLimited {
			status = 429
			headers.Set("Retry-After", "60")
			raw = []byte(`{"message":"rate limited"}`)
			break
		}
		if request.Method == http.MethodPost {
			f.posts++
			var body map[string]any
			decoder := json.NewDecoder(request.Body)
			decoder.UseNumber()
			if e := decoder.Decode(&body); e != nil {
				return nil, e
			}
			number := 1000 + f.posts
			labels := []map[string]any{}
			for _, value := range body["labels"].([]any) {
				labels = append(labels, map[string]any{"name": value})
			}
			created := map[string]any{"number": number, "state": "open", "title": body["title"], "body": body["body"], "labels": labels, "created_at": "2026-10-17T01:00:00Z", "html_url": fmt.Sprintf("https://github.com/example/test/issues/%d", number)}
			f.items = append(f.items, created)
			raw, _ = json.Marshal(created)
			status = 201
			if f.unknownAfterCommit {
				status = 503
				f.unknownAfterCommit = false
			}
		} else {
			result := []map[string]any{}
			if strings.HasSuffix(request.URL.Path, "/issues") {
				for _, v := range f.items {
					if request.URL.Query().Get("labels") == "company-request" {
						labels := v["labels"].([]map[string]any)
						candidate := false
						for _, label := range labels {
							candidate = candidate || label["name"] == "company-request"
						}
						if !candidate {
							continue
						}
					}
					result = append(result, v)
				}
			}
			raw, _ = json.Marshal(result)
		}
	default:
		return nil, fmt.Errorf("unexpected test endpoint")
	}
	return &http.Response{StatusCode: status, Header: headers, Body: io.NopCloser(bytes.NewReader(raw)), Request: request}, nil
}
func runTestInventoryCLI(t *testing.T, feed *inventoryCLIFeed, cache, mode string) map[string]any {
	t.Helper()
	dir := t.TempDir()
	companies, boards := filepath.Join(dir, "companies.csv"), filepath.Join(dir, "boards.csv")
	if e := os.WriteFile(companies, []byte("slug,name,website\n"), 0600); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(boards, []byte("company_slug,board_slug,board_url,monitor_type,monitor_config\n"), 0600); e != nil {
		t.Fatal(e)
	}
	t.Setenv("JOBSEEK_ATS_TEST_TOKEN", "test-ephemeral-token")
	args := []string{"--cache-dir", cache, "--impact", "--candidate-issues", mode, "--companies-file", companies, "--boards-file", boards, "--github-token-env", "JOBSEEK_ATS_TEST_TOKEN", "--impact-free-reserve-mib", "0", "--queue-jitter-min-seconds", "0", "--queue-jitter-max-seconds", "0"}
	var output bytes.Buffer
	if e := runATSInventory(context.Background(), args, feed, &output); e != nil {
		t.Fatal(e)
	}
	var event map[string]any
	if e := json.NewDecoder(&output).Decode(&event); e != nil {
		t.Fatal("CLI completion event unreadable", e)
	}
	if event["event"] != "ats_inventory.complete" {
		t.Fatal("host status event missing")
	}
	report, ok := event["report"].(map[string]any)
	if !ok {
		t.Fatal("CLI report unreadable")
	}

	if strings.Contains(output.String(), "test-ephemeral-token") {
		t.Fatal("credential exposed in report")
	}
	return report
}
func TestCLIPlansRefillsAndDeduplicatesWithoutPython(t *testing.T) {
	feed := &inventoryCLIFeed{oracle: loadSourceOracle(t)}
	cache := t.TempDir()
	dry := runTestInventoryCLI(t, feed, cache, "dry-run")["candidate_issues"].(map[string]any)
	if dry["status"] != "dry_run" || dry["selected_candidates"] != float64(1) || feed.posts != 0 {
		t.Fatal("dry run did not preserve queue policy")
	}
	first := runTestInventoryCLI(t, feed, cache, "refill")["candidate_issues"].(map[string]any)
	if first["created"] != float64(1) || feed.posts != 1 {
		t.Fatal("refill did not create exactly one fake issue")
	}
	second := runTestInventoryCLI(t, feed, cache, "refill")["candidate_issues"].(map[string]any)
	if second["created"] != float64(1) || feed.posts != 2 {
		t.Fatal("next refill lost remaining candidate or duplicated first")
	}
	third := runTestInventoryCLI(t, feed, cache, "refill")["candidate_issues"].(map[string]any)
	if third["created"] != float64(0) || third["status"] != "candidate_pool_exhausted" || feed.posts != 2 {
		t.Fatal("durable ledger allowed repeated creation")
	}
}
func TestCLIReconcilesUnknownCreateAndStopsOnRateLimit(t *testing.T) {
	feed := &inventoryCLIFeed{oracle: loadSourceOracle(t), unknownAfterCommit: true}
	result := runTestInventoryCLI(t, feed, t.TempDir(), "refill")["candidate_issues"].(map[string]any)
	actions := result["actions"].([]any)
	if len(actions) != 1 || actions[0].(map[string]any)["action"] != "created_reconciled" || feed.posts != 1 {
		t.Fatal("unknown committed create was not reconciled")
	}
	limited := &inventoryCLIFeed{oracle: loadSourceOracle(t), rateLimited: true}
	result = runTestInventoryCLI(t, limited, t.TempDir(), "refill")["candidate_issues"].(map[string]any)
	if result["status"] != "rate_limited_preflight" || limited.posts != 0 || result["retry_after"] != float64(60) {
		t.Fatal("rate limit did not stop creates")
	}
}
func TestCLICoverageQuarantineKeepsSupportPlan(t *testing.T) {
	feed := &inventoryCLIFeed{oracle: loadSourceOracle(t), generation: 1}
	report := runTestInventoryCLI(t, feed, t.TempDir(), "dry-run")
	support := report["support_issues"].(map[string]any)
	actions := support["actions"].([]any)
	candidate := report["candidate_issues"].(map[string]any)
	if len(actions) != 1 || actions[0].(map[string]any)["action"] != "would_create" || candidate["status"] != "coverage_quarantined" || feed.posts != 0 {
		t.Fatal("unknown family escaped quarantine or support planning")
	}
}
func TestTokenFileAndExplicitRegistryContracts(t *testing.T) {
	t.Setenv("JOBSEEK_ATS_MISSING_TOKEN", "")
	path := filepath.Join(t.TempDir(), "token")
	if e := os.WriteFile(path, []byte("test-only-secret\n"), 0600); e != nil {
		t.Fatal(e)
	}
	if value, e := readInventoryGitHubToken("JOBSEEK_ATS_MISSING_TOKEN", path); e != nil || value != "test-only-secret" {
		t.Fatal("ephemeral token file unreadable")
	}
	if e := os.WriteFile(path, []byte("first\nsecond\n"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e := readInventoryGitHubToken("JOBSEEK_ATS_MISSING_TOKEN", path); e == nil {
		t.Fatal("multiline token accepted")
	}
	link := path + "-link"
	if e := os.Symlink(path, link); e != nil {
		t.Fatal(e)
	}
	if _, e := readInventoryGitHubToken("JOBSEEK_ATS_MISSING_TOKEN", link); e == nil {
		t.Fatal("symlink token accepted")
	}
	if a, b, e := resolveInventoryRegistryPaths("explicit-companies", "explicit-boards"); e != nil || a != "explicit-companies" || b != "explicit-boards" {
		t.Fatal("explicit registry paths probed installed data")
	}
}
