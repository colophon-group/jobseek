package worker

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Exercise canonical effects of the actual Python traversal cases frozen in
// rss_paged_stream_test.go: a stream spans pages, while browser attempts are atomic.
func TestRealGroupedRSSPaginationCanonicalSettlement(t *testing.T) {
	for _, kind := range []string{"generic", "summary", "wp_job_manager"} {
		for _, rendered := range []bool{false, true} {
			for _, mode := range []string{"complete", "late_xml", "repeat", "limit", "later_reserved"} {
				if kind == "wp_job_manager" && mode == "limit" {
					continue
				}
				t.Run(fmt.Sprintf("%s/rendered=%v/%s", kind, rendered, mode), func(t *testing.T) {
					preset := kind
					if kind == "summary" {
						preset = "generic"
					}
					md := `{"preset":"` + preset + `","feed_url":"https://example.com/feed","scraper_type":"skip"`
					if preset == "generic" {
						md += `,"pagination":{"param_name":"page","page_size":100,"max_pages":2}`
					}
					if kind == "summary" {
						md += `,"description_mode":"title_employment_location"`
					}
					worker := queue.Simple
					if rendered {
						md += `,"render":true`
						worker = queue.Browser
					}
					md += `}`
					f := privateRichPipelineFixture(t, "rss", md, worker)
					ctx := context.Background()
					claim, err := f.a.Claim(ctx, worker)
					if err != nil || claim == nil {
						t.Fatal("RSS claim unavailable", err)
					}
					circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
					if err != nil {
						t.Fatal(err)
					}
					requests := 0
					pageBody := func(page int) string {
						count, offset := 120, 0
						if mode == "later_reserved" && page == 1 {
							count = 220
						}
						if page == 2 {
							offset, count = 120, 1
							if mode == "late_xml" || mode == "limit" {
								count = 100
							}
							if mode == "repeat" {
								count, offset = 120, 0
							}
						}
						var body strings.Builder
						body.WriteString(`<rss><channel>`)
						for n := 0; n < count; n++ {
							description := `<![CDATA[<p>Build reliable systems in Python.</p>]]>`
							if kind == "summary" {
								description = "Senior Software Engineer | Full Time | Zurich"
							}
							fmt.Fprintf(&body, `<item><link>https://example.com/job/%s/%d</link><title>Senior Software Engineer</title><description>%s</description></item>`, f.company, offset+n, description)
						}
						body.WriteString(`</channel></rss>`)
						if page == 2 && mode == "late_xml" {
							body.WriteString("<broken")
						}
						return body.String()
					}
					client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
						if rendered {
							t.Error("browser feed used direct HTTP")
						}
						requests++
						param := "page"
						if preset == "wp_job_manager" {
							param = "paged"
						}
						page, _ := strconv.Atoi(r.URL.Query().Get(param))
						if page != requests {
							t.Error("page order differs", r.URL)
						}
						if page == 2 && mode == "later_reserved" {
							w.Header().Set("TDM-Reservation", "1")
						}
						fmt.Fprint(w, pageBody(page))
					})
					renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
						return collectRSSPages(ctx, p.Endpoint, p.RSSPagination, true, func(ctx context.Context, endpoint string) (RichDiscovery, error) {
							requests++
							body := pageBody(requests)
							value := heldRenderedResult("", endpoint, 200)
							value.GetSuccess().Captures = []*runtimev1.CapturedValue{{CaptureId: "feed", Body: heldRenderedResult(body, endpoint, 200).GetSuccess().Html}}
							if requests == 2 && mode == "later_reserved" {
								v := "1"
								value.GetSuccess().ResourcePolicy.TdmReservationHeader = &v
							}
							return parseRenderedRSSPage(ctx, endpoint, value, preset, kind == "summary")
						})
					})
					result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
					if err != nil || result == nil || !result.Settled || requests != 2 {
						t.Fatal("paginated feed failed settlement", result, err, requests)
					}
					assertRichDeadlineAndLease(t, f, "rss", worker)
					var inserted, missing, failures int
					var reserved bool
					if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); err != nil {
						t.Fatal(err)
					}
					if err = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
						t.Fatal(err)
					}
					if err = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
						t.Fatal(err)
					}
					want, status, wantMissing, wantFailures := 200, "failed", 0, 1
					if rendered {
						want = 0
					}
					if mode == "repeat" && !rendered {
						// The committed 200 entries contain only 120 distinct URLs.
						want = 120
					}
					if mode == "complete" {
						want, status, wantMissing, wantFailures = 121, "succeeded", 1, 0
					}
					if mode == "later_reserved" {
						want, status, wantFailures = 0, "publisher_reserved", 0
						if !rendered {
							want = 200
						}
					}
					if inserted != want || missing != wantMissing || failures != wantFailures || reserved != (mode == "later_reserved") || result.Cycle.Status != status {
						t.Fatal("pagination canonical outcome differs", inserted, missing, failures, reserved, result.Cycle, result.Batches)
					}
					if mode == "complete" {
						var title string
						var description, employment *string
						if err = f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html,p.employment_type FROM job_posting p LEFT JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid LIMIT 1", f.board, f.original).Scan(&title, &description, &employment); err != nil {
							t.Fatal(err)
						}
						if title != "Senior Software Engineer" {
							t.Fatal(title)
						}
						if kind == "summary" {
							if description != nil || employment == nil || *employment != "full_time" {
								t.Fatal("summary lost canonical fields")
							}
						} else if description == nil || !strings.Contains(*description, "Build reliable systems in Python.") {
							t.Fatal("raw CDATA content lost")
						}
					}
				})
			}
		}
	}
}
