package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealOwnedSuccessFactorsFeedPersistsRichJobsAndRejectsPartialXML(t *testing.T) {
	for _, assignment := range []string{"skip", "json-ld", "dom"} {
		for _, mode := range []string{"complete", "partial_xml", "404", "reserved"} {
			t.Run(assignment+"/"+mode, func(t *testing.T) {
				f := privateRichPipelineFixture(t, "rss", `{"preset":"successfactors","variant":"feed","feed_url":"https://example.com/googlefeed.xml","scraper_type":"`+assignment+`"}`)
				ctx := context.Background()
				claim, err := f.a.Claim(ctx, queue.Simple)
				if err != nil || claim == nil {
					t.Fatal("native SuccessFactors claim unavailable", err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				postingURL := "https://example.com/job/" + f.company
				description := "<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>"
				requests := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					requests++
					if r.URL.Path != "/googlefeed.xml" || r.URL.RawQuery != "" {
						t.Error("SuccessFactors feed binding differs")
					}
					if mode == "404" {
						w.WriteHeader(404)
						return
					}
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "https://example.com/policy")
						return
					}
					_, _ = fmt.Fprintf(w, `<rss xmlns:g="http://base.google.com/ns/1.0"><channel><item><link>%s</link><title>Senior Software Engineer (Zurich, Switzerland)</title><description><![CDATA[%s]]></description><g:location>Zurich, Switzerland</g:location></item>`, postingURL, description)
					if mode == "partial_xml" {
						_, _ = w.Write([]byte(`<item><link>broken`))
					} else {
						_, _ = w.Write([]byte(`</channel></rss>`))
					}
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled || requests != 1 {
					t.Fatalf("native SuccessFactors cycle did not settle: %+v %v", result, err)
				}
				assertRichDeadlineAndLease(t, f, "rss")
				var reserved bool
				var gone, failures int
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,gone_confirmation_count,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &gone, &failures); err != nil {
					t.Fatal(err)
				}
				if gone != 0 || reserved != (mode == "reserved") || failures != map[string]int{"complete": 0, "partial_xml": 1, "404": 1, "reserved": 0}[mode] {
					t.Fatal("SuccessFactors failure/publisher result differs", reserved, gone, failures)
				}
				if mode != "complete" {
					var count, active int
					if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 || result.Batches.Inserted != 0 {
						t.Fatal("partial/failed RSS inventory changed postings", err)
					}
					return
				}
				var title, currency, html string
				var locations, technologies []int32
				var uploaded bool
				if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],p.salary_currency,p.location_ids,p.technology_ids,d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.source_url=$1`, postingURL).Scan(&title, &currency, &locations, &technologies, &html, &uploaded); err != nil {
					t.Fatal(err)
				}
				if result.Batches.Inserted != 1 || result.Cycle.Gone != 1 || title != "Senior Software Engineer" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || fmt.Sprint(technologies) != "[4]" || html != description || uploaded {
					t.Fatal("native SuccessFactors fields/description/lifecycle differ")
				}
			})
		}
	}
}
