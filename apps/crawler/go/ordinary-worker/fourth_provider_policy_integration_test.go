package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"sync"
	"testing"
	"time"
)

func fourthReservedResponse(t *testing.T) json.RawMessage {
	t.Helper()
	raw, e := json.Marshal(map[string]any{"status": 200, "body": "", "headers": map[string]string{"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"}})
	if e != nil {
		t.Fatal(e)
	}
	return raw
}

func TestRealFourthProviderMonitorReservationPreservesCanonicalContent(t *testing.T) {
	for _, scenario := range []string{"rippling", "paycom-bootstrap", "paycom-search"} {
		t.Run(scenario, func(t *testing.T) {
			provider := "paycom"
			if scenario == "rippling" {
				provider = "rippling"
			}
			var c fourthHTTPCase
			for _, ref := range fourthHTTPCases(t) {
				if ref.Provider == provider && ref.Name == "complete" {
					c = ref
					break
				}
			}
			metadata, e := json.Marshal(map[string]any{"scraper_type": provider})
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, provider, string(metadata), c.BoardURL)
			ctx := context.Background()
			if _, e = f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); e != nil {
				t.Fatal(e)
			}
			resource := c.Requests[0].URL
			if scenario == "paycom-search" {
				resource = c.Requests[len(c.Requests)-1].URL
			}
			c.Pages[resource] = fourthReservedResponse(t)
			claim, circuits := claimFixture(t, f)
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			result, e := RunGreenhouseClaim(ctx, f.a, claim, fourthHTTPFixture(t, c.Pages, &requests, &mutex), richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("reservation failed to settle", e)
			}
			var active, reserved bool
			var missing, count, failures int
			var recorded string
			if e = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,tdm_reservation->>'url' FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &recorded); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); e != nil {
				t.Fatal(e)
			}
			if !active || missing != 3 || !reserved || failures != 0 || count != 1 || result.Batches.Inserted != 0 || recorded != resource || requests[len(requests)-1].URL != resource {
				t.Fatal("publisher reservation changed inventory or lost resource")
			}
			assertRichDeadlineAndLease(t, f, provider)
		})
	}
}

func TestRealFourthProviderDetailReservationPreservesCanonicalContent(t *testing.T) {
	for _, scenario := range []string{"rippling", "paycom-bootstrap", "paycom-detail"} {
		t.Run(scenario, func(t *testing.T) {
			provider := "paycom"
			if scenario == "rippling" {
				provider = "rippling"
			}
			var c fourthDetailCase
			for _, ref := range fourthDetailCases(t) {
				if ref.Provider == provider && ref.Name == "complete" {
					c = ref
					break
				}
			}
			metadata, e := json.Marshal(map[string]any{"scraper_type": provider, "scraper_config": c.Config})
			if e != nil {
				t.Fatal(e)
			}
			f, a, claim := independentDetailOwnedFixture(t, string(metadata), c.URL)
			ctx := context.Background()
			resource := c.Requests[0].URL
			if scenario == "paycom-detail" {
				resource = c.Requests[len(c.Requests)-1].URL
			}
			c.Pages[resource] = fourthReservedResponse(t)
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			result, e := RunDetail(ctx, a, claim, fourthHTTPFixture(t, c.Pages, &requests, &mutex), richPipelinePreparer(t, f).Processor, circuits)
			if e != nil || result == nil || !result.Settled || result.Cycle.Status != "publisher_reserved" {
				t.Fatal("detail reservation did not settle", result, e)
			}
			var title, recorded string
			var active, reserved bool
			var failures, descriptions int
			var due time.Time
			if e = f.pg.QueryRow(ctx, "SELECT titles[1],is_active,tdm_reserved,scrape_failures,next_scrape_at,tdm_reservation->>'url',(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid", f.original).Scan(&title, &active, &reserved, &failures, &due, &recorded, &descriptions); e != nil {
				t.Fatal(e)
			}
			if title != "Original" || !active || !reserved || failures != 0 || descriptions != 0 || recorded != resource || requests[len(requests)-1].URL != resource {
				t.Fatal("reservation changed canonical detail or lost source", title, recorded)
			}
			score, e := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
			if e != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("reservation lost deadline or lease", e)
			}
		})
	}
}
