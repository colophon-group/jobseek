package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestRealGroupedGenericInventoriesCanonicalWritesAndFailureConservation(t *testing.T) {
	for _, provider := range []string{"dom", "rss", "inline"} {
		for _, mode := range []string{"complete", "failed", "reserved"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				md := map[string]any{"scraper_type": "skip"}
				switch provider {
				case "dom":
					md["scraper_type"] = "json-ld"
					md["url_filter"] = "/jobs/"
					md["pagination"] = map[string]any{"param_name": "page", "max_pages": 3}
				case "rss":
					md["preset"] = "generic"
					md["feed_url"] = "https://example.com/feed?department=eng"
				case "inline":
					md["steps"] = []map[string]string{{"tag": "h2", "field": "title"}, {"tag": "p", "field": "description"}}
					md["fetch_urls"] = []map[string]any{{"url": "https://example.com/alternate", "headers": map[string]string{"X-No-Cache": "true"}}}
				}
				raw, e := json.Marshal(md)
				if e != nil {
					t.Fatal(e)
				}
				f := privateRichPipelineFixture(t, provider, string(raw))
				ctx := context.Background()
				if _, e = f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); e != nil {
					t.Fatal(e)
				}
				claim, circuits := claimFixture(t, f)
				requests := 0
				source := "https://example.com/jobs/" + f.company
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests++
					if provider == "dom" && r.URL.Query().Get("page") == "" {
						fmt.Fprintf(w, `<a href=%q>Engineer</a>`, source)
						return
					}
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "https://example.com/policy")
					}
					if mode == "failed" {
						w.WriteHeader(500)
						return
					}
					switch provider {
					case "dom":
						if r.URL.Query().Get("page") == "2" {
							fmt.Fprintf(w, `<a href="%s/second">Second</a>`, source)
						} else {
							w.WriteHeader(404)
						}
					case "rss":
						fmt.Fprintf(w, `<rss><channel><item><link>%s</link><title>Engineer</title><description><![CDATA[<p>Build products</p>]]></description><location>Zurich</location><guid>123</guid></item></channel></rss>`, source)
					case "inline":
						if r.Header.Get("X-No-Cache") != "true" {
							t.Error("inline cache bypass header lost")
						}
						fmt.Fprint(w, `<h2>Engineer</h2><p>Build products</p>`)
					}
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("grouped generic claim did not settle", e)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var count, active, failures, gone int
				var reserved bool
				if e = f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &gone, &reserved); e != nil {
					t.Fatal(e)
				}
				if mode != "complete" {
					if count != 1 || active != 1 || result.Batches.Inserted != 0 || gone != 0 || failures != map[string]int{"failed": 1, "reserved": 0}[mode] || reserved != (mode == "reserved") {
						t.Fatal("failed/reserved inventory changed canonical membership", count, active, failures, gone, reserved)
					}
					return
				}
				want := 1
				if provider == "dom" {
					want = 2
				}
				if count != want+1 || active != want || result.Batches.Inserted != want || result.Cycle.Gone != 1 || failures != 0 || gone != 0 || reserved {
					t.Fatal("complete generic inventory effects differ", count, active, result.Batches, result.Cycle)
				}
				if provider == "dom" {
					var scheduled int
					if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND is_active AND next_scrape_at IS NOT NULL", f.board).Scan(&scheduled); e != nil || scheduled != 2 {
						t.Fatal("DOM pagination did not independently schedule both details", scheduled, e)
					}
					if requests != 3 {
						t.Fatal("later 404 became board gone")
					}
				}
				if provider == "rss" || provider == "inline" {
					var title, html string
					wantHTML := "<p>Build products</p>"
					if provider == "inline" {
						wantHTML = "Build products"
					}
					if e = f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.is_active", f.board).Scan(&title, &html); e != nil || title != "Engineer" || html != wantHTML {
						t.Fatal("rich generic fields/descriptions differ", title, html, e)
					}
				}
			})
		}
	}
}
