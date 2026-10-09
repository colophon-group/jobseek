package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealDOMPaginatedTotalsAndNegativeCursorConservation(t *testing.T) {
	for _, proxy := range []bool{false, true} {
		for _, mode := range []string{"complete", "mismatch", "missing-total", "conflicting-total", "late-failure", "late-policy", "negative-cursor", "jsonld-filter"} {
			t.Run(fmt.Sprintf("proxy=%v/%s", proxy, mode), func(t *testing.T) {
				md := map[string]any{"scraper_type": "json-ld", "link_selector": "a.job", "pagination": map[string]any{"param_name": "page", "max_pages": 4, "transport_attempts": 1}, "advertised_total": map[string]string{"selector": ".total", "regex": "([0-9]+) jobs"}}
				if mode == "negative-cursor" {
					md["pagination"].(map[string]any)["start"] = -1
					delete(md, "advertised_total")
				}
				if mode == "jsonld-filter" {
					md["require_jsonld_jobposting"] = true
				}
				if proxy {
					md["proxy"] = true
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixture(t, "dom", string(raw))
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				var listingCalls, detailCalls atomic.Int64
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path != "/careers" {
						detailCalls.Add(1)
						if mode != "jsonld-filter" || !strings.HasPrefix(r.URL.Path, "/jobs/") {
							t.Error("unbound detail request")
						}
						if strings.HasSuffix(r.URL.Path, "-two") {
							fmt.Fprint(w, "<p>No JobPosting</p>")
							return
						}
						fmt.Fprint(w, `<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer"}</script>`)
						return
					}
					call := listingCalls.Add(1)
					if call == 1 {
						if r.URL.RawQuery != "" {
							t.Error("root cursor changed")
						}
						total := "2"
						if mode == "mismatch" {
							total = "3"
						}
						if mode != "missing-total" {
							fmt.Fprintf(w, `<div class="total">%s jobs</div>`, total)
						}
						if mode == "conflicting-total" {
							fmt.Fprint(w, `<div class="total">3 jobs</div>`)
						}
						fmt.Fprintf(w, `<a class="job" href="/jobs/%s-one">Engineer</a>`, f.company)
						return
					}
					wantCursor := fmt.Sprint(call)
					if mode == "negative-cursor" {
						wantCursor = fmt.Sprint(call - 2)
					}
					if r.URL.Query().Get("page") != wantCursor {
						t.Error("pagination cursor changed")
					}
					if call == 2 {
						if mode == "late-failure" {
							w.WriteHeader(500)
							return
						}
						if mode == "late-policy" {
							w.Header().Set("TDM-Reservation", "1")
							w.WriteHeader(403)
							return
						}
						fmt.Fprintf(w, `<a class="job" href="/jobs/%s-one">Duplicate</a><a class="job" href="/jobs/%s-two">Other</a>`, f.company, f.company)
						return
					}
					w.WriteHeader(404)
				}))
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("pagination settlement lost", e)
				}
				var inserted, missing, failures int
				var reserved bool
				var evidence *string
				if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,tdm_reservation::text,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT missing_count FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &evidence, &inserted, &missing); e != nil {
					t.Fatal(e)
				}
				accepted := mode == "complete" || mode == "negative-cursor" || mode == "jsonld-filter"
				if accepted {
					wantInserted := 2
					if mode == "jsonld-filter" {
						wantInserted = 1
					}
					if inserted != wantInserted || missing != 1 || failures != 0 || reserved || listingCalls.Load() != 3 {
						t.Fatal("complete inventory changed", inserted, missing, failures, listingCalls.Load())
					}
					if f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != int64(wantInserted) {
						t.Fatal("detail handoff lost")
					}
					if mode == "jsonld-filter" && detailCalls.Load() != 2 {
						t.Fatal("whole total checked after detail filtering", detailCalls.Load())
					}
				} else {
					if inserted != 0 || missing != 0 || detailCalls.Load() != 0 {
						t.Fatal("partial inventory changed canonical jobs")
					}
					if mode == "late-policy" {
						if !reserved || failures != 0 || evidence == nil || !strings.Contains(*evidence, "page=2") {
							t.Fatal("tail policy lost resource identity")
						}
					} else if reserved || failures != 1 {
						t.Fatal("unproved listing finalized", failures, reserved)
					}
				}
				assertRichDeadlineAndLease(t, f, "dom")
			})
		}
	}
}

func TestRealAPIImplicitURLPreservesExplicitFieldsAcrossTransports(t *testing.T) {
	for _, route := range []string{"direct", "proxy", "rendered"} {
		for _, mode := range []string{"canonical", "fallback", "field-list", "partial", "publisher", "failed"} {
			t.Run(route+"/"+mode, func(t *testing.T) {
				md := map[string]any{"api_url": "https://example.com/api", "json_path": "jobs", "fields": map[string]any{"title": "title", "description": "body", "locations": "city"}, "scraper_type": "skip", "transport_attempts": 1}
				if mode == "field-list" {
					md["fields"].(map[string]any)["description"] = []any{map[string]any{"path": "body", "html_unescape": true}, map[string]any{"path": "extra", "html_unescape": true}}
				}
				if mode == "partial" {
					md["total_path"] = "total"
				}
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
				// Raw order puts artwork and apply first; canonical must win.
				row := fmt.Sprintf(`{"pictureUrl":"https://example.com/logo.png","applyUrl":"https://example.com/apply/%s","links":{"canonical":"/jobs/%s"},"title":"Engineer","body":"<p>Build systems.</p>","city":"Zurich"}`, f.company, f.company)
				if mode == "fallback" {
					row = fmt.Sprintf(`{"mystery":"https://example.com/jobs/%s","title":"Engineer","body":"<p>Build systems.</p>","city":"Zurich"}`, f.company)
				}
				if mode == "field-list" {
					row = strings.Replace(row, `"body":"<p>Build systems.</p>"`, `"body":"&lt;p&gt;Build systems.&lt;/p&gt;","extra":"&lt;p&gt;Learning.&lt;/p&gt;"`, 1)
				}
				body := []byte(`{"jobs":[` + row + `]}`)
				if mode == "partial" {
					body = []byte(`{"jobs":[` + row + `],"total":10}`)
				}
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
						inv, e := api.DiscoverBrowserReplay(ctx, o, func(context.Context, api.Request) (*api.Document, error) { calls++; return api.Decode(body) }, api.PythonJoinURL, false)
						if e != nil {
							return RichDiscovery{}, e
						}
						response.Inventory, _ = json.Marshal(inv)
					}
					return parseAPIReplayResponse(p, response)
				})
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				wantCalls := 1
				if mode == "failed" && route != "rendered" {
					wantCalls = 3
				}
				if e != nil || result == nil || !result.Settled || calls != wantCalls {
					t.Fatal("API settlement changed", e, calls)
				}
				var inserted, missing, failures int
				var reserved bool
				if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT missing_count FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &inserted, &missing); e != nil {
					t.Fatal(e)
				}
				if mode == "publisher" || mode == "failed" {
					if inserted != 0 || missing != 0 || reserved != (mode == "publisher") || failures != map[bool]int{true: 1, false: 0}[mode == "failed"] {
						t.Fatal("rejected API inventory changed canonical state")
					}
				} else {
					wantMissing := 1
					if mode == "partial" {
						wantMissing = 0
					}
					var url, title, html string
					var nextNull bool
					if e := f.pg.QueryRow(ctx, "SELECT p.source_url,p.titles[1],d.html,p.next_scrape_at IS NULL FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&url, &title, &html, &nextNull); e != nil {
						t.Fatal(e)
					}
					if inserted != 1 || missing != wantMissing || failures != 0 || reserved || url != "https://example.com/jobs/"+f.company || title != "Engineer" || !strings.Contains(html, "Build systems.") || !nextNull {
						t.Fatal("explicit fields or implicit URL changed", url, html, missing)
					}
					if mode == "field-list" && (!strings.Contains(html, "<p>Learning.</p>") || strings.Contains(html, "&lt;p&gt;")) {
						t.Fatal("API list extraction/html unescape lost", html)
					}
					if len(f.r.Keys(ctx, "ft_scrapes_*").Val()) != 0 {
						t.Fatal("rich inventory created independent detail work")
					}
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer", worker)
			})
		}
	}
}
