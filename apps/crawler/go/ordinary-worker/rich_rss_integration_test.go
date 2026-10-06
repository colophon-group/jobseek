package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealOwnedTeamtailorPersistsRichJobsAndProtectsIncompleteInventory(t *testing.T) {
	for _, assignment := range []string{"skip", "json-ld", "dom"} {
		for _, mode := range []string{"complete", "later404", "later_reserved"} {
			t.Run(assignment+"/"+mode, func(t *testing.T) {
				f := privateRichPipelineFixture(t, "rss", `{"preset":"teamtailor","feed_url":"https://example.com/jobs.rss","scraper_type":"`+assignment+`"}`)
				ctx := context.Background()
				claim, err := f.a.Claim(ctx, queue.Simple)
				if err != nil || claim == nil {
					t.Fatal("native RSS claim unavailable", err)
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
					if r.URL.Path != "/jobs.rss" || r.URL.Query().Get("per_page") != "100" {
						t.Error("RSS feed/page binding changed")
					}
					if requests == 1 {
						if mode != "complete" {
							_, _ = w.Write([]byte(teamtailorRSSPage(100)))
							return
						}
						_, _ = fmt.Fprintf(w, `<rss xmlns:tt="https://teamtailor.com/locations"><channel><item><link>%s</link><title>Senior Software Engineer</title><description><![CDATA[%s]]></description><remoteStatus>fully</remoteStatus><tt:locations><tt:location><tt:name>Zurich</tt:name></tt:location></tt:locations></item></channel></rss>`, postingURL, description)
						return
					}
					if mode == "later404" {
						w.WriteHeader(404)
						return
					}
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://example.com/policy")
					_, _ = w.Write([]byte(teamtailorRSSPage(0)))
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatalf("native RSS claim did not settle: %+v %v", result, err)
				}
				assertRichDeadlineAndLease(t, f, "rss")
				var reserved bool
				var gone, failures int
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,gone_confirmation_count,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &gone, &failures); err != nil {
					t.Fatal(err)
				}
				if gone != 0 || reserved != (mode == "later_reserved") || failures != map[string]int{"complete": 0, "later404": 1, "later_reserved": 0}[mode] {
					t.Fatal("RSS failure/publisher outcome changed", reserved, gone, failures)
				}
				if mode != "complete" {
					var count, active int
					if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 || result.Batches.Inserted != 0 || requests != 2 {
						t.Fatal("incomplete RSS inventory changed canonical postings", err)
					}
					return
				}
				var title, currency, html string
				var locations, technologies []int32
				var locationTypes []string
				var uploaded bool
				if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],p.salary_currency,p.location_ids,p.technology_ids,p.location_types,d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.source_url=$1`, postingURL).Scan(&title, &currency, &locations, &technologies, &locationTypes, &html, &uploaded); err != nil {
					t.Fatal(err)
				}
				if requests != 1 || result.Batches.Inserted != 1 || result.Cycle.Gone != 1 || title != "Senior Software Engineer" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || fmt.Sprint(technologies) != "[4]" || fmt.Sprint(locationTypes) != "[remote]" || html != description || uploaded {
					t.Fatal("native RSS fields/description/lifecycle effects differ")
				}
			})
		}
	}
}
