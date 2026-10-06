package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestRealFifthProviderMonitorReferencesCommitCanonicalContentAndSettlement(t *testing.T) {
	for _, c := range fifthExecutionCases(t) {
		if c.Detail {
			continue
		}
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			metadata := c.Metadata
			metadata["scraper_type"] = c.Provider
			if c.Provider == "cornerstone" {
				metadata["scraper_type"] = "skip"
			} else {
				metadata["scraper_config"] = map[string]any{"enrich": []string{"description"}}
			}
			raw, e := json.Marshal(metadata)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, c.Provider, string(raw), c.Source)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			result, e := RunGreenhouseClaim(ctx, f.a, claim, fifthExecutionHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("provider did not settle", result, e)
			}
			assertRichDeadlineAndLease(t, f, c.Provider)
			var failures, gone int
			var reserved bool
			if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &gone, &reserved); e != nil {
				t.Fatal(e)
			}
			if reserved {
				t.Fatal("reference invented reservation")
			}
			if c.Expected.Gone {
				if failures != 0 || gone != 1 || result.Batches.Inserted != 0 {
					t.Fatal("gone changed inventory")
				}
				return
			}
			if c.Expected.Error {
				if failures != 1 || gone != 0 || result.Batches.Inserted != 0 {
					t.Fatal("failed inventory wrote content")
				}
				return
			}
			if failures != 0 || gone != 0 {
				t.Fatal("valid inventory failed")
			}
			for _, source := range c.Expected.URLs {
				var id, title string
				var due bool
				if e = f.pg.QueryRow(ctx, "SELECT id::text,titles[1],next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id, &title, &due); e != nil {
					t.Fatal(e)
				}
				var ref map[string]any
				for _, r := range c.Expected.Jobs {
					if r["url"] == source {
						ref = r
						break
					}
				}
				if ref == nil || title != ref["title"] {
					t.Fatal("canonical title differs")
				}
				if (c.Provider != "cornerstone") != due {
					t.Fatal("detail schedule differs")
				}
				if description, ok := ref["description"].(string); ok && description != "" {
					var html string
					if e = f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid LIMIT 1", id).Scan(&html); e != nil || !strings.Contains(html, description) {
						t.Fatal("canonical body differs", e)
					}
				}
			}
		})
	}
}
func TestRealFifthProviderDetailReferencesCommitCanonicalContentAndSettlement(t *testing.T) {
	for _, c := range fifthExecutionCases(t) {
		if !c.Detail {
			continue
		}
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			raw, e := json.Marshal(map[string]any{"scraper_type": c.Provider, "scraper_config": c.Metadata})
			if e != nil {
				t.Fatal(e)
			}
			f, a, claim := independentDetailOwnedFixture(t, string(raw), c.Source)
			ctx := context.Background()
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			result, e := RunDetail(ctx, a, claim, fifthExecutionHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f).Processor, circuits)
			status := "succeeded"
			if c.Expected.Error || c.Expected.Empty {
				status = "failed"
			}
			if e != nil || result == nil || !result.Settled || result.Cycle.Status != status {
				t.Fatal("detail terminal contract differs", result, e)
			}
			var title string
			var active, reserved bool
			var failures, descriptions int
			var due time.Time
			if e = f.pg.QueryRow(ctx, "SELECT titles[1],is_active,tdm_reserved,scrape_failures,next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid", f.original).Scan(&title, &active, &reserved, &failures, &due, &descriptions); e != nil {
				t.Fatal(e)
			}
			wantTitle, wantFailures := "Original", 1
			if status == "succeeded" {
				wantTitle, _ = c.Expected.Content["title"].(string)
				wantTitle = strings.TrimSpace(wantTitle)
				wantFailures = 0
			}
			if title != wantTitle || !active || reserved || failures != wantFailures {
				t.Fatal("canonical detail differs", title, failures)
			}
			if status == "failed" && descriptions != 0 {
				t.Fatal("failure wrote body")
			}
			if status == "succeeded" {
				if c.Expected.CanonicalDescription != nil && *c.Expected.CanonicalDescription != "" {
					var html string
					if e = f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid LIMIT 1", f.original).Scan(&html); e != nil || !strings.Contains(html, *c.Expected.CanonicalDescription) {
						t.Fatal("detail body differs", html, e)
					}
				}
			}
			score, e := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
			if e != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("detail queue conservation differs", e)
			}
		})
	}
}
