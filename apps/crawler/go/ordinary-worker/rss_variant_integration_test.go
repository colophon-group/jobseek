package worker

import (
	"context"
	"fmt"
	"hash/crc32"
	"net/http"
	"strings"
	"testing"
)

func TestRealGroupedRSSVariantsPersistFieldsAndRejectInvalidInventory(t *testing.T) {
	for _, kind := range []string{"legacy_xml", "summary"} {
		for _, assignment := range []string{"skip", "json-ld"} {
			for _, mode := range []string{"complete", "partial", "reserved"} {
				t.Run(kind+"/"+assignment+"/"+mode, func(t *testing.T) {
					md := `{"preset":"successfactors","variant":"legacy_xml","company":"Fixture","feed_url":"https://career.example.com/career?company=Fixture&career_ns=job_listing_summary&resultType=XML","scraper_type":"` + assignment + `"}`
					if kind == "summary" {
						md = `{"preset":"generic","description_mode":"title_employment_location","feed_url":"https://example.com/feed","scraper_type":"` + assignment + `"}`
					}
					f := privateRichPipelineFixture(t, "rss", md)
					ctx := context.Background()
					claim, circuits := claimFixture(t, f)
					id := fmt.Sprint(crc32.ChecksumIEEE([]byte(f.company)))
					u := "https://career.example.com/sfcareer/jobreqcareer?jobId=" + id + "&company=Fixture"
					body := `<?xml version="1.0"?><Jobs><Job><ReqId>` + id + `</ReqId><JobTitle>Senior Software Engineer</JobTitle><Job-Description><![CDATA[<p>Build reliable systems in Python.</p>]]></Job-Description><filter8><value>Zurich</value></filter8><filter4><value>Remote</value></filter4></Job></Jobs>`
					if kind == "summary" {
						u = "https://example.com/job/" + f.company
						body = `<rss><channel><item><link>` + u + `</link><title>Senior Software Engineer</title><description>Senior Software Engineer | Full Time | Zurich</description></item></channel></rss>`
					}
					if mode == "partial" {
						body += "<broken"
					}
					requests := 0
					client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
						requests++
						if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
							return
						}
						_, _ = w.Write([]byte(body))
					})
					result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
					if err != nil || result == nil || !result.Settled || requests != 1 {
						t.Fatal("variant cycle did not settle", result, err, requests)
					}
					assertRichDeadlineAndLease(t, f, "rss")
					var failures, missing, inserted int
					var reserved bool
					if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
						t.Fatal(err)
					}
					if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, u).Scan(&inserted); err != nil {
						t.Fatal(err)
					}
					if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
						t.Fatal(err)
					}
					if mode != "complete" {
						if inserted != 0 || missing != 0 || reserved != (mode == "reserved") || failures != map[string]int{"partial": 1, "reserved": 0}[mode] {
							t.Fatal("invalid inventory changed canonical state", inserted, missing, reserved, failures)
						}
						return
					}
					var title string
					var locations []int
					var employment, description *string
					var uploaded *bool
					var locationTypes []string
					if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],p.location_ids,p.employment_type,p.location_types,d.html,d.r2_uploaded FROM job_posting p LEFT JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2 LIMIT 1", f.board, u).Scan(&title, &locations, &employment, &locationTypes, &description, &uploaded); err != nil {
						t.Fatal(err)
					}
					if inserted != 1 || failures != 0 || reserved || title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" {
						t.Fatal("variant canonical fields differ", inserted, failures, title, locations)
					}
					if kind == "summary" {
						if employment == nil || *employment != "full_time" || description != nil {
							t.Fatal("structured summary stored incomplete description or lost employment", employment, description)
						}
					} else if description == nil || !strings.Contains(*description, "Build reliable systems in Python.") || uploaded == nil || *uploaded || fmt.Sprint(locationTypes) != "[remote]" {
						t.Fatal("legacy XML description/remote/upload fields differ", description, uploaded, locationTypes)
					}
				})
			}
		}
	}
}
