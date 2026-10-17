package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"

	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealQualifiedWixHTTPMonitorPreservesCompleteInventoryAndFailureEffects(t *testing.T) {
	for _, mode := range []string{"success", "pagination", "empty", "root-policy", "api-policy-error", "seed404", "api404", "challenge", "ambiguous-bootstrap", "missing-token", "missing-total", "null-items", "foreign-link", "duplicate-id", "short-page", "later-failure", "total-drift"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "dom", `{"render":true,"actions":[{"action":"click","selector":"#more"}],"scraper_type":"json-ld"}`, queue.Browser, queue.Simple)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("browser claim unavailable", err)
			}
			config := claim.Descriptor().Config
			p, err := queue.InspectRichMonitor(f.board, config)
			if err != nil {
				t.Fatal(err)
			}
			qualifiedWixDOMHTTPMonitors[p.EffectiveConfigSHA256] = domHTTPMonitorRoute{"example.com", "/careers"}
			defer delete(qualifiedWixDOMHTTPMonitors, p.EffectiveConfigSHA256)
			changed := map[string]string{}
			for k, v := range config {
				changed[k] = v
			}
			changed["check_interval_minutes"] = "123"
			foreign := p
			foreign.Endpoint = "https://foreign.example/careers"
			if qualifiedWixDOMHTTPMonitor(p, changed) || qualifiedWixDOMHTTPMonitor(foreign, config) {
				t.Fatal("changed full config or foreign source granted replacement")
			}
			const grid = "00000000-0000-4000-8000-000000000001"
			const instance = "fixture-public-wix-session-instance"
			calls, pages := 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "example.com" || r.Header.Get("Cookie") != "" {
					t.Error("request changed bound method, source or anonymous session")
				}
				switch r.URL.Path {
				case "/careers":
					if r.Header.Get("Authorization") != "" {
						t.Error("session header escaped its API resource")
					}
					w.Header().Set("Content-Type", "text/html")
					if mode == "root-policy" {
						w.Header().Set("TDM-Reservation", "1")
					}
					if mode == "challenge" {
						fmt.Fprint(w, `<title>Just a moment...</title>`)
						return
					}
					fmt.Fprintf(w, `<script type="application/json">{"siteFeaturesConfigs":{"dataWixCodeSdk":{"gridAppId":%q}}}</script>`, grid)
					if mode == "ambiguous-bootstrap" {
						fmt.Fprint(w, `<script type="application/json">{"siteFeaturesConfigs":{"dataWixCodeSdk":{"gridAppId":"00000000-0000-4000-8000-000000000002"}}}</script>`)
					}
				case "/_api/v1/access-tokens":
					if r.Header.Get("Authorization") != "" {
						t.Error("query authorization leaked to bootstrap")
					}
					w.Header().Set("Content-Type", "application/json")
					if mode == "seed404" {
						w.WriteHeader(404)
						return
					}
					if mode == "missing-token" {
						fmt.Fprint(w, `{"apps":{}}`)
						return
					}
					fmt.Fprintf(w, `{"apps":{"fixture-data-binding":{"intId":49,"instance":%q}}}`, instance)
				case "/_api/cloud-data/v2/items/query":
					pages++
					if r.Header.Get("Authorization") != instance {
						t.Error("fresh session did not reach its bound query")
					}
					raw, err := base64.StdEncoding.DecodeString(r.URL.Query().Get(".r"))
					var query struct {
						Collection string `json:"dataCollectionId"`
						App        string `json:"appId"`
						Query      struct {
							Paging struct{ Offset, Limit int } `json:"paging"`
						} `json:"query"`
					}
					if err != nil || json.Unmarshal(raw, &query) != nil || query.Collection != "Jobs" || query.App != grid || query.Query.Paging.Limit != 100 {
						t.Error("query changed bound collection, tenant or budget")
					}
					w.Header().Set("Content-Type", "application/json")
					if mode == "api404" {
						w.WriteHeader(404)
						return
					}
					if mode == "api-policy-error" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(403)
						return
					}
					if mode == "missing-total" {
						fmt.Fprint(w, `{"dataItems":[],"pagingMetadata":{"count":0,"offset":0}}`)
						return
					}
					if mode == "null-items" {
						fmt.Fprint(w, `{"dataItems":null,"pagingMetadata":{"count":0,"offset":0,"total":0}}`)
						return
					}
					total := 1
					if mode == "pagination" || mode == "later-failure" || mode == "total-drift" {
						total = 101
					}
					if mode == "empty" {
						total = 0
					}
					if mode == "duplicate-id" {
						total = 2
					}
					offset := query.Query.Paging.Offset
					if offset > 0 && mode == "later-failure" {
						w.WriteHeader(503)
						return
					}
					if offset > 0 && mode == "total-drift" {
						total = 100
					}
					count := max(0, min(100, total-offset))
					items := []map[string]any{}
					for i := 0; i < count; i++ {
						id := offset + i + 1
						if mode == "duplicate-id" {
							id = 1
						}
						link := fmt.Sprintf("/jobs/wix-qualified-%d", offset+i)
						if mode == "foreign-link" {
							link = "https://foreign.example/jobs/rejected"
						}
						items = append(items, map[string]any{"id": fmt.Sprintf("%08x-0000-4000-8000-%012x", id, id), "data": map[string]string{"link-jobs-title": link}})
					}
					if mode == "short-page" {
						items = items[:0]
					}
					json.NewEncoder(w).Encode(map[string]any{"dataItems": items, "pagingMetadata": map[string]int{"offset": offset, "count": len(items), "total": total}})
				default:
					t.Error("request outside qualified resources")
					w.WriteHeader(404)
				}
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			reader := &NativeRenderedDetails{client: &lp.Client{}}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, reader)
			if err != nil || result == nil || !result.Settled || calls == 0 {
				t.Fatal("qualified query did not settle", err)
			}
			var active, reserved bool
			var missing, count, failures int
			if err = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &count); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "success", "pagination":
				want := 2
				if mode == "pagination" {
					want = 102
					if pages != 2 {
						t.Fatal("complete pagination did not use two bounded pages")
					}
				}
				if count != want || active || missing != 4 || failures != 0 {
					t.Fatal("complete inventory changed canonical lifecycle", count, active, missing, failures)
				}
			case "empty":
				if count != 1 || !active || missing != 3 || failures != 0 {
					t.Fatal("verified zero bypassed the shared empty/drop guard")
				}
			case "root-policy", "api-policy-error":
				if !reserved || !active || missing != 3 || count != 1 || failures != 0 || result.Cycle.Status != "publisher_reserved" {
					t.Fatal("publisher reservation changed canonical inventory")
				}
			default:
				if !active || missing != 3 || count != 1 || failures == 0 {
					t.Fatal("failed or partial query changed canonical inventory", count, active, missing, failures)
				}
			}
			if f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "monitors_simple:dom").Val() != 0 {
				t.Fatal("original browser lane or settlement changed")
			}
		})
	}
}
