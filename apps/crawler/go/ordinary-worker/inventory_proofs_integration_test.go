package worker

import (
	"context"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealDOMAPIConfiguredInventoryProofs(t *testing.T) {
	for _, route := range []string{"dom", "dom-rendered", "api_sniffer"} {
		provider := strings.TrimSuffix(route, "-rendered")
		rendered := route == "dom-rendered"
		for _, mode := range []string{"zero", "positive", "unproved", "reserved", "missing-response"} {
			t.Run(route+"/"+mode, func(t *testing.T) {
				metadata := `{"scraper_type":"skip","link_selector":"a.job","url_filter":"/jobs/","empty_states":[{"selector":".empty","exact_text":"No jobs","forbidden_link_selector":"a.job"}],"advertised_total":{"selector":".total","regex":"([0-9]+) jobs"}}`
				if provider == "api_sniffer" {
					metadata = `{"scraper_type":"skip","api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title","description":"body"},"empty_response":{"found_jobs":false,"jobs":[]}}`
				}
				worker := queue.Simple
				if rendered {
					worker = queue.Browser
					metadata = strings.Replace(metadata, `{"scraper_type":`, `{"render":true,"scraper_type":`, 1)
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, "https://example.com/careers", worker)
				ctx := context.Background()
				claim, err := f.a.Claim(ctx, worker)
				if err != nil || claim == nil {
					t.Fatal("proof claim missing", err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				var requests atomic.Int32
				handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests.Add(1)
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						if rendered {
							fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
						}
					}
					if mode == "missing-response" {
						w.WriteHeader(404)
						return
					}
					if provider == "api_sniffer" {
						if mode == "positive" {
							fmt.Fprint(w, `{"jobs":[{"url":"https://example.com/jobs/new","title":"Engineer","body":"<p>Build</p>"}]}`)
							return
						}
						marker := "false"
						if mode == "unproved" {
							marker = "0"
						}
						fmt.Fprintf(w, `{"jobs":[],"found_jobs":%s}`, marker)
						return
					}
					if mode == "positive" {
						fmt.Fprint(w, `<div class="total">1 jobs</div><a class="job" href="/jobs/new">Engineer</a>`)
						return
					}
					marker := "No jobs"
					if mode == "unproved" {
						marker = "Please retry"
					}
					fmt.Fprintf(w, `<div class="total">0 jobs</div><div class="empty">%s</div>`, marker)
				})
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if rendered {
						t.Error("rendered proof used direct HTTP")
					}
					handler.ServeHTTP(w, r)
				}))
				renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
					response := httptest.NewRecorder()
					handler.ServeHTTP(response, httptest.NewRequest("GET", p.Endpoint, nil))
					return parseHeldRenderedMonitor(ctx, p, c, heldRenderedResult(response.Body.String(), p.Endpoint, uint32(response.Code)))
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("proof settlement lost", err, result)
				}
				assertRichDeadlineAndLease(t, f, provider, worker)
				var active, reserved bool
				var failures, gone int
				if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); err != nil {
					t.Fatal(err)
				}
				failure := mode == "unproved" || mode == "missing-response"
				wantFailures := 0
				if failure {
					wantFailures = 1
				}
				wantGone := 0
				if provider == "dom" && mode == "missing-response" {
					wantFailures = 0
					wantGone = 1
				}
				if reserved != (mode == "reserved") || failures != wantFailures || gone != wantGone {
					t.Fatal("proof outcome differs", reserved, failures, gone)
				}
				if (mode == "reserved" || failure) && (!active || result.Batches.Inserted != 0) {
					t.Fatal("unproved/reserved inventory changed canonical content")
				}
				if mode == "zero" && (result.Discovered != 0 || result.Cycle == nil || result.Cycle.Status != "succeeded") {
					t.Fatal("proved empty inventory failed canonical success")
				}
				if mode == "positive" && result.Batches.Inserted != 1 {
					t.Fatal("positive inventory lost")
				}
				if provider == "api_sniffer" && mode == "missing-response" && requests.Load() != 3 {
					t.Fatal("configured empty response missing retry contract", requests.Load())
				}
			})
		}
	}
}
