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

func TestRealAutomaticAPIFieldsDirectProxyAndRenderedConservation(t *testing.T) {
	for _, route := range []string{"direct", "proxy", "rendered"} {
		for _, mode := range []string{"rich", "url-only", "ambiguous", "empty"} {
			t.Run(route+"/"+mode, func(t *testing.T) {
				md := `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","scraper_type":"skip"}`
				worker := queue.Simple
				if route == "proxy" {
					md = proxyFixtureMetadata(t, md, true)
				}
				if route == "rendered" {
					worker = queue.Browser
					md = strings.Replace(md, `{"api_url":`, `{"browser":true,"api_url":`, 1)
				}
				f := privateRichPipelineFixture(t, "api_sniffer", md, worker)
				ctx := context.Background()
				claim, e := f.a.Claim(ctx, worker)
				if e != nil || claim == nil {
					t.Fatal(e)
				}
				circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if e != nil {
					t.Fatal(e)
				}
				row := map[string]any{"url": "https://example.com/jobs/" + f.company, "title": "Senior Software Engineer", "description": "<p>Build Go services in Zurich.</p>", "location": "Zurich", "employmentType": "Full-time", "workplaceType": "remote"}
				if mode == "url-only" {
					row = map[string]any{"url": row["url"], "identifier": "1"}
				}
				if mode == "ambiguous" {
					row["name"] = "Conflicting title"
				}
				jobs := []any{row}
				if mode == "empty" {
					jobs = []any{}
				}
				body, _ := json.Marshal(map[string]any{"jobs": jobs})
				requests := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests++
					if route == "rendered" || r.URL.Path != "/api" {
						t.Error("unbound request")
					}
					w.Write(body)
				}))
				if route == "proxy" {
					client = credentialedProxyFixture(t, client)
				}
				renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
					o, e := queue.APISnifferBrowserMonitorOptions(c)
					if e != nil {
						return RichDiscovery{}, e
					}
					inventory, e := api.DiscoverBrowserReplay(ctx, o, func(context.Context, api.Request) (*api.Document, error) { requests++; return api.Decode(body) }, api.PythonJoinURL, false)
					response := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success"}
					if e != nil {
						response.Outcome = "failed"
					} else {
						response.Inventory, _ = json.Marshal(inventory)
					}
					return parseAPIReplayResponse(p, response)
				})
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if e != nil || result == nil || !result.Settled || requests != 1 {
					t.Fatal("automatic mapping did not settle", e, result, requests)
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer", worker)
				var inserted, missing, failures int
				if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures); e != nil {
					t.Fatal(e)
				}
				if mode == "ambiguous" {
					if inserted != 0 || missing != 0 || failures != 1 || result.Cycle.Status != "failed" {
						t.Fatal("ambiguous map changed canonical state", inserted, missing, failures)
					}
					return
				}
				want := 1
				if mode == "empty" {
					want = 0
				}
				wantMissing := 1
				if mode == "empty" {
					wantMissing = 0
				}
				if inserted != want || missing != wantMissing || failures != 0 {
					t.Fatal("complete inventory conservation", inserted, missing, failures, want)
				}
				if mode == "rich" {
					var title, description, employment string
					var locations []int32
					var locationTypes []string
					e = f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html,p.location_ids,p.employment_type,p.location_types FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&title, &description, &locations, &employment, &locationTypes)
					if e != nil || title != "Senior Software Engineer" || !strings.Contains(description, "Build Go services in Zurich.") || fmt.Sprint(locations) != "[2]" || employment != "full_time" || fmt.Sprint(locationTypes) != "[remote]" {
						t.Fatal("inferred canonical content changed", e, title, locations, employment, locationTypes)
					}
				}
			})
		}
	}
}
