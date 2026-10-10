package worker

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

func TestLufthansaPublishedAPIOriginalInventoryAndSourceConfig(t *testing.T) {
	directory := os.Getenv("JOBSEEK_INTERACTION_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires private original browser and HTTP API qualification")
	}
	raw, err := os.ReadFile(filepath.Join(directory, "native1005-lufthansa-published-api-original-replay1-2026-10-10.json"))
	if err != nil {
		t.Fatal("private original oracle unavailable")
	}
	var c struct {
		Status          string
		Board           map[string]string
		URLs            []string
		Truncated       bool
		OldBrowserExact bool `json:"old_browser_exact_url_equality"`
		Exchanges       []lastHTTPExchange
		Metadata        map[string]any `json:"monitor_metadata"`
	}
	if json.Unmarshal(raw, &c) != nil || c.Status != "complete" || !c.OldBrowserExact || len(c.URLs) != 373 || len(c.Exchanges) != 1 {
		t.Fatal("complete original oracle unavailable")
	}
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	var metadata map[string]any
	for _, row := range rows[1:] {
		if row[1] == "lufthansa-group-careers" {
			if row[2] != c.Board["board_url"] || row[3] != "api_sniffer" || row[5] != "json-ld" || row[6] != "" || json.Unmarshal([]byte(row[4]), &metadata) != nil {
				t.Fatal("reviewed source configuration changed")
			}
		}
	}
	if metadata == nil {
		t.Fatal("source configuration missing")
	}
	metadata["scraper_type"] = "json-ld"
	if !reflect.DeepEqual(metadata, c.Metadata) {
		t.Fatal("source metadata differs from original reviewed API contract")
	}
	p, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", c.Board)
	if err != nil || queue.MonitorWorker(p) != queue.Simple {
		t.Fatal("compiled HTTP profile rejected", err)
	}
	calls := 0
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		x := c.Exchanges[0]
		body, _ := io.ReadAll(r.Body)
		if calls != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL || string(body) != x.RequestBody {
			t.Error("original published API request changed")
			w.WriteHeader(400)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	found, err := discoverAPISnifferInventory(context.Background(), client, p, c.Board)
	if err != nil || found.Truncated || calls != 1 || len(found.Jobs) != 373 {
		t.Fatal("complete original API inventory changed", err, len(found.Jobs))
	}
	got := []string{}
	for _, job := range found.Jobs {
		if !job.URLOnly || job.Title != nil || job.Description != nil {
			t.Fatal("URL-only monitor acquired rich-content authority")
		}
		got = append(got, job.URL)
	}
	sort.Strings(got)
	sort.Strings(c.URLs)
	if !reflect.DeepEqual(got, c.URLs) {
		t.Fatal("exact original 373 browser URLs changed")
	}
	t.Log("published API matches all 373 original browser and Python HTTP monitor URLs; one request; original independent detail authority retained")
}

func TestRealLufthansaPublishedAPIURLOnlySettlement(t *testing.T) {
	directory := os.Getenv("JOBSEEK_INTERACTION_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires private original browser and HTTP API qualification")
	}
	raw, err := os.ReadFile(filepath.Join(directory, "native1005-lufthansa-published-api-original-replay1-2026-10-10.json"))
	if err != nil {
		t.Fatal("private original oracle unavailable")
	}
	var c struct {
		Board     map[string]string
		URLs      []string
		Exchanges []lastHTTPExchange
	}
	if json.Unmarshal(raw, &c) != nil || len(c.URLs) != 373 || len(c.Exchanges) != 1 {
		t.Fatal("original public oracle incomplete")
	}
	f := privateRichPipelineFixtureURL(t, "api_sniffer", c.Board["metadata"], c.Board["board_url"])
	claim, circuits := claimFixture(t, f)
	calls := 0
	ctx := context.Background()
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		x := c.Exchanges[0]
		if calls != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL {
			t.Error("original published request changed")
			w.WriteHeader(400)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
	if err != nil || result == nil || !result.Settled || result.Batches.Inserted != 373 || calls != 1 {
		t.Fatal("original URL-only inventory did not settle", err)
	}
	assertRichDeadlineAndLease(t, f, "api_sniffer")
	var count, scheduled, rich, missing int
	if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE next_scrape_at IS NOT NULL),count(*) FILTER(WHERE cardinality(titles)>0) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count, &scheduled, &rich); err != nil {
		t.Fatal(err)
	}
	if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
		t.Fatal(err)
	}
	if count != 373 || scheduled != 373 || rich != 0 || missing != 1 {
		t.Fatal("URL-only database or original detail authority changed", count, scheduled, rich, missing)
	}
	for _, source := range c.URLs {
		var id string
		if err := f.pg.QueryRow(ctx, "SELECT id::text FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id); err != nil {
			t.Fatal(err)
		}
		if f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source || f.r.HGet(ctx, "scrape:"+id, "board_id").Val() != f.board {
			t.Fatal("original independent detail route not conserved")
		}
	}
	t.Log("373 original URL-only rows and 373 independent detail routes settled with canonical deadline and zero retained completion lease")
}
