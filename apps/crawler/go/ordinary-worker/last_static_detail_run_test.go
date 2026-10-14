package worker

import (
	"context"
	"encoding/json"
	"fmt"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealLastStaticDetailIndependentCanonicalSettlement(t *testing.T) {
	traces := readLastStaticHTTP(t)
	for _, provider := range []string{"onlyfy", "tupu360", "phuketall"} {
		modes := []string{"complete", "complete_selected", "404", "410", "503_reserved", "foreign_source"}
		for _, mode := range modes {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				var trace lastStaticHTTPTrace
				for _, candidate := range traces {
					if candidate.Provider == provider && candidate.Name == "200" {
						trace = candidate
					}
				}
				source := trace.Source
				md := map[string]any{"scraper_type": provider}
				if mode == "complete_selected" {
					md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
				}
				encoded, _ := json.Marshal(md)
				u, _ := url.Parse(source)
				f, owned := independentDetailOwnedSetup(t, string(encoded), source, queue.Simple, "https://"+u.Host+"/jobs")
				claim, err := owned.Claim(context.Background(), queue.Simple)
				if err != nil || claim == nil {
					t.Fatal("claim missing", err)
				}
				ctx := context.Background()
				needsProxy, err := runtimeClaimUsesProxy(ctx, owned, claim)
				if err != nil || needsProxy {
					t.Fatal("direct ownership lost", needsProxy, err)
				}
				body := trace.Requests[0].Body
				expected := trace.Content
				if mode == "foreign_source" {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url='https://foreign.example/job/1' WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				}
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					want := lastStaticProfile(t, trace)
					if "https://"+r.Host+r.URL.String() != want.Endpoint || r.Method != "GET" {
						t.Error("unbound public detail request", r.Host, r.URL)
					}
					raw := body
					status := 200
					switch mode {
					case "404":
						status = 404
					case "410":
						status = 410
					case "empty":
						raw = ""
					case "503_reserved":
						status = 503
						w.Header().Set("TDM-Reservation", "1")
					}
					w.Header().Set("Content-Type", "text/html; charset=utf-8")
					w.WriteHeader(status)
					_, _ = w.Write([]byte(raw))
				}))
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				result, err := RunDetail(ctx, owned, claim, client, richPipelinePreparer(t, f).Processor, circuits)
				if mode == "foreign_source" {
					var unchangedTitle string
					var descriptions int
					if e := f.pg.QueryRow(ctx, "SELECT titles[1],(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid", f.original).Scan(&unchangedTitle, &descriptions); e != nil {
						t.Fatal(e)
					}
					if err == nil || !strings.Contains(err.Error(), "authority_lost") || calls != 0 || unchangedTitle != "Original" || descriptions != 0 {
						t.Fatal("foreign source gained IO/write authority", err, calls, unchangedTitle, descriptions)
					}
					return
				}
				if err != nil || result == nil || !result.Settled {
					t.Fatal("detail did not settle", result, err)
				}
				var title string
				var active, reserved bool
				var descriptions int
				var due *time.Time
				if err := f.pg.QueryRow(ctx, `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &descriptions); err != nil {
					t.Fatal(err)
				}
				if strings.HasPrefix(mode, "complete") {
					wantTitle := fmt.Sprint(expected["title"])
					if mode == "complete_selected" {
						wantTitle = "Original"
					}
					if title != wantTitle || !active || reserved || due == nil || descriptions != 1 || result.Cycle.Status != "succeeded" {
						t.Fatal("canonical fields/schedule differ", title, active, reserved, due, descriptions, result.Cycle.Status)
					}
					wantHTML, err := enrichment.NormalizeDescriptionHTML(fmt.Sprint(expected["description"]))
					if err != nil || wantHTML == nil {
						t.Fatal(err)
					}
					var html string
					if err := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&html); err != nil || wantHTML == nil || html != *wantHTML {
						t.Fatal("description differs", err, html)
					}
				} else {
					if title != "Original" || descriptions != 0 {
						t.Fatal("failed or reserved detail wrote content", title, descriptions)
					}
					if !active {
						t.Fatal("failed/policy detail removed job")
					}
					if strings.Contains(mode, "reserved") && (!reserved || result.Cycle.Status != "publisher_reserved") {
						t.Fatal("publisher policy not settled", reserved, result.Cycle.Status)
					}
				}
				wantCalls := 1
				if mode == "foreign_source" {
					wantCalls = 0
				}
				if calls != wantCalls || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
					t.Fatal("request repeated or lease retained", calls)
				}
			})
		}
	}
}
