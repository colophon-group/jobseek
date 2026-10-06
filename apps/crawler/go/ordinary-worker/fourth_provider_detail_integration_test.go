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

func TestRealFourthProviderDetailReferencesCommitCanonicalContentAndSettlement(t *testing.T) {
	for _, c := range fourthDetailCases(t) {
		if c.Name == "invalid-defaults" {
			continue
		}
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			metadata, e := json.Marshal(map[string]any{"scraper_type": c.Provider, "scraper_config": c.Config})
			if e != nil {
				t.Fatal(e)
			}
			f, a, claim := independentDetailOwnedFixture(t, string(metadata), c.URL)
			ctx := context.Background()
			if _, e = f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],employment_type='part_time',location_ids=ARRAY[2] WHERE id=$1::uuid", f.original); e != nil {
				t.Fatal(e)
			}
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			client := fourthHTTPFixture(t, c.Pages, &requests, &mutex)
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			result, e := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			status := "succeeded"
			if c.Expected.Error || c.Expected.Empty {
				status = "failed"
			}
			if e != nil || result == nil || !result.Settled || result.Cycle.Status != status {
				t.Fatal("detail terminal contract differs", result, e)
			}
			var titles []string
			var failures int
			var due time.Time
			var active, reserved bool
			if e = f.pg.QueryRow(ctx, "SELECT titles,scrape_failures,next_scrape_at,is_active,tdm_reserved FROM job_posting WHERE id=$1::uuid", f.original).Scan(&titles, &failures, &due, &active, &reserved); e != nil {
				t.Fatal(e)
			}
			wantTitle := "Monitor title"
			wantFailures := 1
			if status == "succeeded" {
				wantTitle, _ = c.Expected.Content["title"].(string)
				wantFailures = 0
			}
			if len(titles) != 1 || titles[0] != strings.TrimSpace(wantTitle) || failures != wantFailures || !active || reserved {
				t.Fatal("canonical content or failures differ", titles, failures, active, reserved)
			}
			if status == "succeeded" {
				var html string
				if e = f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid ORDER BY locale LIMIT 1", f.original).Scan(&html); e != nil || !strings.Contains(html, "<p>") {
					t.Fatal("detail description staging lost", e)
				}
				if desc, ok := c.Expected.Content["description"].(string); ok && !strings.Contains(html, desc) {
					t.Fatal("canonical body differs from Python detail", html)
				}
				// The actual legacy processor derives SQL compensation from description
				// text; the flat upstream base_salary DTO is preserved by the adapter.
				var salaryMin, salaryMax *int64
				var salaryCurrency, salaryPeriod *string
				if e = f.pg.QueryRow(ctx, "SELECT salary_min,salary_max,salary_currency,salary_period FROM job_posting WHERE id=$1::uuid", f.original).Scan(&salaryMin, &salaryMax, &salaryCurrency, &salaryPeriod); e != nil {
					t.Fatal(e)
				}
				if salaryMin != nil || salaryMax != nil || salaryCurrency != nil || salaryPeriod != nil {
					t.Fatal("description-only salary derivation changed")
				}

			} else {
				var n int
				if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&n); e != nil || n != 0 {
					t.Fatal("failure wrote content", e)
				}
			}
			score, e := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
			if e != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("deadline or claim conservation differs", e)
			}
		})
	}
}
