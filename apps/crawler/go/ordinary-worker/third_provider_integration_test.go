package worker

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"
)

func TestRealThirdProviderReferenceHTTPCommitsCanonicalEffects(t *testing.T) {
	for _, c := range thirdHTTPCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			md := map[string]any{}
			if json.Unmarshal(c.Metadata, &md) != nil {
				t.Fatal("invalid metadata")
			}
			// The exact current scraper configurations are tested separately; these
			// references isolate canonical monitor settlement and URL-only scheduling.
			md["scraper_type"] = "skip"
			if c.Provider == "jobvite" {
				md["scraper_type"] = "json-ld"
			}
			raw, e := json.Marshal(md)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, c.Provider, string(raw), c.BoardURL)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			requests := []secondaryHTTPRequest{}
			var mutex sync.Mutex
			result, e := RunGreenhouseClaim(ctx, f.a, claim, secondaryHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("provider did not settle canonically", e)
			}
			assertRichDeadlineAndLease(t, f, c.Provider)
			var failures, gone int
			var reserved bool
			if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &gone, &reserved); e != nil {
				t.Fatal(e)
			}
			if reserved {
				t.Fatal("reference invented publisher reservation")
			}
			if c.Expected.Gone {
				if failures != 0 || gone != 1 || result.Batches.Inserted != 0 {
					t.Fatal("gone became content/failure")
				}
				return
			}
			if c.Expected.Error {
				if failures != 1 || gone != 0 || result.Batches.Inserted != 0 {
					t.Fatal("failed inventory reached canonical content")
				}
				return
			}
			if failures != 0 || gone != 0 {
				t.Fatal("valid inventory recorded as failure")
			}
			for _, source := range c.Expected.URLs {
				var id string
				var title *string
				var due bool
				if e := f.pg.QueryRow(ctx, "SELECT id::text,titles[1],next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id, &title, &due); e != nil {
					t.Fatal(e)
				}
				if c.Provider == "jobvite" {
					if title != nil || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
						t.Fatal("URL-only source lost native detail scheduling")
					}
					continue
				}
				var ref map[string]any
				for _, job := range c.Expected.Jobs {
					if job["url"] == source {
						ref = job
					}
				}
				if ref == nil || title == nil || *title != ref["title"] {
					t.Fatal("canonical title differs from actual Python output")
				}
				if due {
					t.Fatal("skip detail assignment invented scrape")
				}
				if description, ok := ref["description"].(string); ok && description != "" {
					var html string
					if e := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid ORDER BY locale LIMIT 1", id).Scan(&html); e != nil {
						t.Fatal(e)
					}
					if !strings.Contains(html, description) {
						t.Fatal("canonical description differs")
					}
				}
			}
		})
	}
}

func TestRealThirdProviderReservationPreservesCanonicalContent(t *testing.T) {
	for _, scenario := range []string{"comeet", "jobvite", "jobvite-category"} {
		provider := scenario
		if scenario == "jobvite-category" {
			provider = "jobvite"
		}
		t.Run(scenario, func(t *testing.T) {
			var c secondaryHTTPCase
			for _, v := range thirdHTTPCases(t) {
				if v.Provider == provider && v.Name == "complete" {
					c = v
					break
				}
			}
			md := map[string]any{"scraper_type": "skip"}
			if provider == "jobvite" {
				md["scraper_type"] = "json-ld"
			}
			raw, e := json.Marshal(md)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, provider, string(raw), c.BoardURL)
			ctx := context.Background()
			if _, e = f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); e != nil {
				t.Fatal(e)
			}
			claim, circuits := claimFixture(t, f)
			requests := []secondaryHTTPRequest{}
			var mutex sync.Mutex
			page, e := json.Marshal(map[string]any{"status": 200, "body": "", "headers": map[string]string{"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"}})
			if e != nil {
				t.Fatal(e)
			}
			c.Pages = map[string]json.RawMessage{c.Endpoint: page}
			if scenario == "jobvite-category" {
				initial, err := json.Marshal(map[string]any{"status": 200, "body": `<html ng-app="jv.careersite.desktop.app"><script>careersiteName: "acme"</script><a href="/acme/search?c=Engineering&amp;p=0">More</a></html>`})
				if err != nil {
					t.Fatal(err)
				}
				c.Pages[c.Endpoint] = initial
				c.Pages[c.Endpoint+"/search?c=Engineering&p=0"] = page
			}
			result, e := RunGreenhouseClaim(ctx, f.a, claim, secondaryHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("publisher reservation did not settle", e)
			}
			var active, reserved bool
			var missing, count int
			if e = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved); e != nil {
				t.Fatal(e)
			}
			if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); e != nil {
				t.Fatal(e)
			}
			if !active || missing != 3 || !reserved || count != 1 || result.Batches.Inserted != 0 {
				t.Fatal("publisher reservation changed posting content")
			}
			assertRichDeadlineAndLease(t, f, provider)
		})
	}
}
