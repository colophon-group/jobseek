package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealAPIItemScopeAndURLPolicyKeepCanonicalFieldsAndSourceGap(t *testing.T) {
	for _, rendered := range []bool{false, true} {
		for _, mode := range []string{"complete", "gap", "reserved", "invalid-identity"} {
			t.Run(fmt.Sprintf("rendered=%v/%s", rendered, mode), func(t *testing.T) {
				md := `{"api_url":"https://example.com/api","json_path":"jobs","total_path":"total","url_field":"url","fields":{"title":"title","description":"body","locations":"city"},"item_filter":{"include":{"scope":["main"]},"require_regex":{"id":"[0-9]+"}},"url_filter":{"include":"/jobs/","exclude":"/intern$"},"scraper_type":"skip"}`
				worker := queue.Simple
				if rendered {
					worker = queue.Browser
					md = strings.Replace(md, `{"api_url":`, `{"browser":true,"api_url":`, 1)
				}
				f := privateRichPipelineFixture(t, "api_sniffer", md, worker)
				ctx := context.Background()
				claim, err := f.a.Claim(ctx, worker)
				if err != nil || claim == nil {
					t.Fatal(err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				total := 3
				if mode == "gap" {
					total = 30
				}
				id := "1"
				if mode == "invalid-identity" {
					id = "bad"
				}
				body := fmt.Sprintf(`{"total":%d,"jobs":[{"id":%q,"scope":"main","url":"https://example.com/jobs/new","title":"Senior Software Engineer","body":"<p>Build Go services in Zurich.</p>","city":"Zurich"},{"id":"2","scope":"main","url":"https://example.com/jobs/intern","title":"Intern","body":"<p>Intern</p>"},{"id":"3","scope":"other","url":"https://example.com/jobs/foreign","title":"Foreign","body":"<p>Foreign</p>"}]}`, total, id)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if rendered {
						t.Error("rendered API scope used direct HTTP")
					}
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
					fmt.Fprint(w, body)
				}))
				renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
					response := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success"}
					if mode == "reserved" {
						response.Outcome = "publisher_reserved"
						response.Reservation = &replay.Reservation{URL: "https://example.com/api", Source: "header"}
						return parseAPIReplayResponse(p, response)
					}
					o, err := queue.APISnifferBrowserMonitorOptions(c)
					if err != nil {
						return RichDiscovery{}, err
					}
					inventory, err := api.Discover(ctx, o.Inventory, func(context.Context, api.Request) (*api.Document, error) { return api.Decode([]byte(body)) }, api.PythonJoinURL)
					if err != nil {
						response.Outcome = "failed"
					} else {
						response.Inventory, _ = json.Marshal(inventory)
					}
					return parseAPIReplayResponse(p, response)
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("filtered API did not settle", err, result)
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer", worker)
				var inserted, missing, failures int
				var reserved bool
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
					t.Fatal(err)
				}
				if mode == "reserved" || mode == "invalid-identity" {
					wantFailures := 0
					if mode == "invalid-identity" {
						wantFailures = 1
					}
					if inserted != 0 || missing != 0 || failures != wantFailures || reserved != (mode == "reserved") {
						t.Fatal("failed/reserved scope changed canonical inventory", inserted, missing, failures, reserved)
					}
					return
				}
				var title, html string
				var locations []int32
				if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],p.location_ids,d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&title, &locations, &html); err != nil {
					t.Fatal(err)
				}
				wantMissing := 1
				if mode == "gap" {
					wantMissing = 0
				}
				if inserted != 1 || missing != wantMissing || failures != 0 || reserved || title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" || !strings.Contains(html, "Build Go services") {
					t.Fatal("URL scope lost fields or hid upstream gap", inserted, missing, failures, title, locations, html)
				}
			})
		}
	}
}
