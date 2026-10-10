package worker

import (
	"context"
	"encoding/json"
	"testing"
)

func TestRealRemainingHTTPProviderCanonicalSettlement(t *testing.T) {
	for _, c := range remainingHTTPInventoryCases(t) {
		if c.Mode != "complete" && !(c.Provider == "jobdiva" && c.Mode == "changing") {
			continue
		}
		for _, mode := range []string{"complete", "failed", "reserved"} {
			t.Run(c.Provider+"/"+c.Mode+"/"+mode, func(t *testing.T) {
				var md map[string]any
				json.Unmarshal(c.Board.Metadata, &md)
				md["scraper_type"] = "json-ld"
				if c.Provider == "johdi" {
					md["scraper_type"] = "johdi"
					md["scraper_config"] = map[string]any{"company_key": md["company_key"], "flow": md["flow"], "locale": md["locale"]}
				}
				if c.Provider == "jobdiva" {
					md["scraper_type"] = "api_sniffer"
					md["scraper_config"] = map[string]any{"api_url": "https://ws.jobdiva.com/candPortal/rest/job/getdetailbyjobid/{id}?compid=-1", "url_pattern": "#/jobs/(?P<id>[0-9]+)", "json_path": "job", "fields": map[string]string{"title": "title", "description": "jobDescription"}}
				}

				if c.Provider == "headhunter" {
					md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixtureURL(t, c.Provider, string(raw), c.Board.URL)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				client, _ := remainingHTTPFixtureClient(t, c, mode)
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("canonical claim did not settle", e)
				}
				assertRichDeadlineAndLease(t, f, c.Provider)
				var failures, missing, count int
				var reserved bool
				if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); e != nil {
					t.Fatal(e)
				}
				wantFailure := 0
				if mode == "failed" {
					wantFailure = 1
				}
				if reserved != (mode == "reserved") || failures != wantFailure {
					t.Fatal("terminal board authority changed", reserved, failures)
				}
				if mode != "complete" {
					if missing != 0 || count != 0 {
						t.Fatal("unproved inventory wrote prefix or absence")
					}
					return
				}
				var expected []any
				if json.Unmarshal(c.Jobs, &expected) != nil || count != len(expected) {
					t.Fatal("canonical inventory lost postings", count, len(expected))
				}
				wantMissing := 1
				if c.Truncated {
					wantMissing = 0
				}
				if missing != wantMissing {
					t.Fatal("complete/truncated absence authority changed", missing, wantMissing)
				}
				for _, value := range expected {
					source, title := "", ""
					if c.Provider == "headhunter" {
						job := value.(map[string]any)
						source = job["url"].(string)
						title = job["title"].(string)
					} else {
						source = value.(string)
					}
					var actualTitle string
					var due bool
					var descriptionCount int
					if e = f.pg.QueryRow(ctx, `SELECT coalesce(titles[1],''),next_scrape_at IS NOT NULL,(SELECT count(*) FROM descriptions d WHERE d.posting_id=p.id) FROM job_posting p WHERE board_id=$1::uuid AND source_url=$2`, f.board, source).Scan(&actualTitle, &due, &descriptionCount); e != nil {
						t.Fatal(e)
					}
					if actualTitle != title || !due || (c.Provider == "headhunter") != (descriptionCount == 1) {
						t.Fatal("canonical fields or scheduled detail intent changed", actualTitle, due, descriptionCount)
					}
				}
			})
		}
	}
}
