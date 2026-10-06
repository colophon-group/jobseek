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

func TestRealFifthProviderMonitorReservationPreservesCanonicalContent(t *testing.T) {
	for _, scenario := range []string{"adp-search", "cornerstone-bootstrap", "cornerstone-search", "paylocity-listing", "paylocity-proxy-listing"} {
		t.Run(scenario, func(t *testing.T) {
			provider := strings.Split(scenario, "-")[0]
			c := fifthPolicyReference(t, provider, false, "complete")
			scraper := provider
			if provider == "cornerstone" {
				scraper = "skip"
			}
			monitorConfig := map[string]any{"scraper_type": scraper}
			if strings.Contains(scenario, "-proxy-") {
				c.Name += "/proxy"
				c.Metadata["proxy"] = true
				monitorConfig["proxy"] = true
			}
			metadata, e := json.Marshal(monitorConfig)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, provider, string(metadata), c.Source)
			ctx := context.Background()
			if _, e = f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); e != nil {
				t.Fatal(e)
			}
			resource := c.Requests[0].URL
			if scenario == "cornerstone-search" {
				resource = c.Requests[len(c.Requests)-1].URL
			}
			c.Pages[resource] = fourthReservedResponse(t)
			claim, circuits := claimFixture(t, f)
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			result, e := RunGreenhouseClaim(ctx, f.a, claim, fifthExecutionHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f), circuits)
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

func TestRealFifthProviderDetailReservationPreservesCanonicalContent(t *testing.T) {
	for _, scenario := range []string{"adp-detail", "adp-document", "paylocity-detail", "paylocity-proxy-detail"} {
		t.Run(scenario, func(t *testing.T) {
			provider := strings.Split(scenario, "-")[0]
			name := "complete"
			if scenario == "adp-document" {
				name = "docx"
			}
			c := fifthPolicyReference(t, provider, true, name)
			if strings.Contains(scenario, "-proxy-") {
				c.Name += "/proxy"
				c.Metadata["proxy"] = true
			}
			metadata, e := json.Marshal(map[string]any{"scraper_type": provider, "scraper_config": c.Metadata})
			if e != nil {
				t.Fatal(e)
			}
			f, a, claim := independentDetailOwnedFixture(t, string(metadata), c.Source)
			ctx := context.Background()
			resource := c.Requests[0].URL
			if scenario == "adp-document" {
				resource = c.Requests[len(c.Requests)-1].URL
			}
			c.Pages[resource] = fourthReservedResponse(t)
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			result, e := RunDetail(ctx, a, claim, fifthExecutionHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f).Processor, circuits)
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

func fifthPolicyReference(t *testing.T, provider string, detail bool, name string) fifthHTTPCase {
	t.Helper()
	for _, ref := range fifthHTTPCases(t) {
		if ref.Provider == provider && ref.Detail == detail && ref.Name == name {
			return ref
		}
	}
	t.Fatal("actual provider reference missing")
	return fifthHTTPCase{}
}
