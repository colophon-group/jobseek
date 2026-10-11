package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type listingInspectPreparer struct {
	delegate RichPreparer
	inspect  func(RichMonitorJob)
}

func (p listingInspectPreparer) Prepare(ctx context.Context, job RichMonitorJob) (*queue.GreenhouseRichContent, error) {
	p.inspect(job)
	return p.delegate.Prepare(ctx, job)
}

func TestRealGroupedListingControlsPersistFailAndSettle(t *testing.T) {
	for _, kind := range []string{"title-scan", "pdf-owner", "pdf-expiry", "api-pdf"} {
		routes := []string{"direct", "proxy"}
		if kind == "pdf-owner" || kind == "pdf-expiry" {
			routes = append(routes, "rendered")
		}
		for _, route := range routes {
			for _, mode := range []string{"complete", "invalid", "publisher"} {
				t.Run(kind+"/"+route+"/"+mode, func(t *testing.T) {
					provider := "dom"
					if kind == "api-pdf" {
						provider = "api_sniffer"
					}
					md := map[string]any{"scraper_type": "json-ld"}
					switch kind {
					case "title-scan":
						md["title_matched_url_scan"] = map[string]any{"listing_title_selector": ".title", "detail_title_selector": "h4", "url_template": "https://example.com/vacante-{index}", "max_scan": 3}
					case "pdf-owner":
						md["link_selector"] = "a.job"
						md["require_pdf_text"] = map[string]any{"include": "Employer", "exclude": "Foreign"}
					case "pdf-expiry":
						md["link_selector"] = "a.job"
						md["require_unexpired_pdf"] = map[string]any{"pattern": `Deadline: (.+)`, "date_format": "%d %B %Y"}
					case "api-pdf":
						md["scraper_type"] = "skip"
						md["api_url"] = "https://example.com/api"
						md["json_path"] = "jobs"
						md["url_field"] = "url"
						md["fields"] = map[string]any{"title": "title", "description": "body"}
						md["require_pdf_pattern"] = "Employer"
						md["require_unexpired_pdf"] = map[string]any{"pattern": `Deadline: (.+)`, "date_format": "%d %B %Y"}
					}
					worker := queue.Simple
					if route == "proxy" {
						md["proxy"] = true
					}
					if route == "rendered" {
						worker = queue.Browser
						md["render"] = true
					}
					raw, _ := json.Marshal(md)
					f := privateRichPipelineFixture(t, provider, string(raw), worker)
					deadline := time.Now().UTC().AddDate(1, 0, 0)
					text := "Employer\nDeadline: " + deadline.Format("2 January 2006")
					if mode == "invalid" {
						text = "Unclassified employer\nNo deadline"
					}
					listingProofCommands(t, text)
					ctx := context.Background()
					claim, e := f.a.Claim(ctx, worker)
					if e != nil || claim == nil {
						t.Fatal("installed opaque claim unavailable", e)
					}
					circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
					if e != nil {
						t.Fatal(e)
					}
					var calls atomic.Int64
					listing := `<div class="title">Engineer</div><a class="job" href="/posting.pdf">Engineer</a>`
					client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						calls.Add(1)
						if r.URL.Path == "/careers" {
							fmt.Fprint(w, listing)
							return
						}
						if r.URL.Path == "/api" {
							fmt.Fprint(w, `{"jobs":[{"url":"https://example.com/posting.pdf","title":"Engineer","body":"<p>Build systems.</p>"}]}`)
							return
						}
						if mode == "publisher" && (r.URL.Path == "/posting.pdf" || r.URL.Path == "/vacante-1") {
							w.Header().Set("TDM-Reservation", "1")
							w.WriteHeader(404)
							return
						}
						if kind == "title-scan" {
							if r.URL.Path != "/vacante-1" {
								w.WriteHeader(404)
								return
							}
							title := "Engineer"
							if mode == "invalid" {
								title = "Unlisted"
							}
							fmt.Fprintf(w, "<h4>%s</h4>", title)
							return
						}
						if r.URL.Path != "/posting.pdf" {
							t.Error("unbound PDF resource", r.URL)
							w.WriteHeader(404)
							return
						}
						w.Header().Set("Content-Type", "application/pdf")
						fmt.Fprint(w, "%PDF bounded fixture")
					}))
					if route == "proxy" {
						client = credentialedProxyFixture(t, client)
					}
					renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
						calls.Add(1)
						return collectRenderedDOMPages(ctx, p, c, client.client, func(int) (*runtimev1.BrowserResult, error) { return heldRenderedResult(listing, p.Endpoint, 200), nil })
					})
					prepared := 0
					preparer := listingInspectPreparer{richPipelinePreparer(t, f), func(job RichMonitorJob) {
						prepared++
						if kind == "api-pdf" && job.Extras["valid_through"] != deadline.Format("2006-01-02") {
							t.Error("original API deadline annotation lost")
						}
					}}
					result, e := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits, renderer)
					if e != nil || result == nil || !result.Settled {
						t.Fatal("grouped claim did not settle", e, result)
					}
					var inserted, missing, failures int
					var reserved bool
					if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT missing_count FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &inserted, &missing); e != nil {
						t.Fatal(e)
					}
					if mode == "complete" {
						if inserted != 1 || missing != 1 || failures != 0 || reserved {
							t.Fatal("complete group inventory lost", inserted, missing, failures, reserved)
						}
						if kind == "api-pdf" {
							var title, html string
							var unscheduled bool
							if e = f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html,p.next_scrape_at IS NULL FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&title, &html, &unscheduled); e != nil || title != "Engineer" || !strings.Contains(html, "Build systems") || prepared != 1 || !unscheduled {
								t.Fatal("API PDF canonical fields changed", title, html, e)
							}
						}
					} else if inserted != 0 || missing != 0 || reserved != (mode == "publisher") || failures != map[bool]int{true: 1, false: 0}[mode == "invalid"] {
						t.Fatal("failed/publisher group changed inventory", inserted, missing, failures, reserved)
					}
					assertRichDeadlineAndLease(t, f, provider, worker)
				})
			}
		}
	}
}
