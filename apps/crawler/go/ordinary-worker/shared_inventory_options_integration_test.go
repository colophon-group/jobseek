package worker

import (
	"context"
	"encoding/json"
	"fmt"
	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"strings"
	"testing"
)

func TestRealDOMAlternateFetchDirectAndProxyConservation(t *testing.T) {
	t.Run("direct", func(t *testing.T) { realDOMTransportCases(t, false, false, true) })
	t.Run("proxy", func(t *testing.T) { realDOMTransportCases(t, true, false, true) })
}
func TestRealAPILegacyRootEnrichPreservesNoDetailScheduling(t *testing.T) {
	t.Run("direct", func(t *testing.T) { realAPIRichTransportCases(t, false, false, true) })
	t.Run("proxy", func(t *testing.T) { realAPIRichTransportCases(t, true, false, true) })
}

func TestRealAPISlugFieldsDirectProxyRenderedTerminalEffects(t *testing.T) {
	for _, route := range []string{"direct", "proxy", "rendered"} {
		for _, mode := range []string{"complete", "publisher", "failed"} {
			t.Run(route+"/"+mode, func(t *testing.T) {
				md := map[string]any{"api_url": "https://example.com/api", "json_path": "jobs", "url_template": "https://example.com/jobs/{id}/{slug}", "slug_fields": []string{"title"}, "fields": map[string]string{"title": "title", "description": "body", "locations": "city"}, "enrich": []string{"description"}, "scraper_type": "skip", "transport_attempts": 1}
				worker := queue.Simple
				if route == "proxy" {
					md["proxy"] = true
				}
				if route == "rendered" {
					md["browser"] = true
					worker = queue.Browser
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixture(t, "api_sniffer", string(raw), worker)
				ctx := context.Background()
				claim, e := f.a.Claim(ctx, worker)
				if e != nil || claim == nil {
					t.Fatal(e)
				}
				circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if e != nil {
					t.Fatal(e)
				}
				body, _ := json.Marshal(map[string]any{"jobs": []any{map[string]any{"id": f.company, "title": "Senior Software Engineer & Platform + été", "body": "<p>Build Go services in Zurich.</p>", "city": "Zurich"}}})
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if route == "rendered" || r.URL.Path != "/api" {
						t.Error("unbound API request")
					}
					if mode == "publisher" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(403)
						return
					}
					if mode == "failed" {
						fmt.Fprint(w, "invalid-json")
						return
					}
					w.Write(body)
				}))
				if route == "proxy" {
					client = credentialedProxyFixture(t, client)
				}
				renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
					response := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success"}
					if mode == "publisher" {
						calls++
						response.Outcome = "publisher_reserved"
						response.Reservation = &replay.Reservation{URL: p.Endpoint, Source: "header"}
					} else if mode == "failed" {
						calls++
						response.Outcome = "failed"
					} else {
						o, e := queue.APISnifferBrowserMonitorOptions(c)
						if e != nil {
							return RichDiscovery{}, e
						}
						inventory, e := api.DiscoverBrowserReplay(ctx, o, func(context.Context, api.Request) (*api.Document, error) { calls++; return api.Decode(body) }, api.PythonJoinURL, false)
						if e != nil {
							return RichDiscovery{}, e
						}
						response.Inventory, _ = json.Marshal(inventory)
					}
					return parseAPIReplayResponse(p, response)
				})
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				wantCalls := 1
				if mode == "failed" && route != "rendered" {
					wantCalls = 3
				}
				if e != nil || result == nil || !result.Settled || calls != wantCalls {
					t.Fatal("slug inventory did not settle", e, calls)
				}
				var inserted, missing, failures int
				var reserved bool
				if e := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &inserted); e != nil {
					t.Fatal(e)
				}
				if mode == "complete" {
					var url, title, html string
					var nextDueNull bool
					if e := f.pg.QueryRow(ctx, "SELECT p.source_url,p.titles[1],d.html,p.next_scrape_at IS NULL FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&url, &title, &html, &nextDueNull); e != nil {
						t.Fatal(e)
					}
					if inserted != 1 || missing != 1 || failures != 0 || reserved || url != "https://example.com/jobs/"+f.company+"/senior-software-engineer-and-platform-plus-ete" || title != "Senior Software Engineer & Platform + été" || !strings.Contains(html, "Build Go services") || !nextDueNull {
						t.Fatal("slug fields or original root enrich scheduling changed", inserted, missing, failures, url, title)
					}
					if len(f.r.Keys(ctx, "ft_scrapes_*").Val()) != 0 {
						t.Fatal("ignored root enrich created detail work")
					}
				} else if inserted != 0 || missing != 0 || mode == "publisher" && (!reserved || failures != 0) || mode == "failed" && (reserved || failures != 1) {
					t.Fatal("failed/reserved inventory changed canonical state")
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer", worker)
			})
		}
	}
}
